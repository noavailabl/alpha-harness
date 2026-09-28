"""Simulation lifecycle tracking.

The one invariant that matters: **a simulation id is never held only in memory.** A
``201`` from ``POST /simulations`` returns the id *only* in the ``Location`` header, and
for a multi-simulation that id is the sole handle that can cancel the batch — lose it and
the simulation runs on, holding a concurrency slot, unstoppable.

So :meth:`SimulationTracker.submit` writes a row *before* the HTTP request and updates
it with the id the instant the response lands. A crash between the two leaves a
``PENDING`` row, which :meth:`reconcile` surfaces rather than silently discards.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import func, select, update

from ..brain.errors import (
    BrainAuthError,
    BrainDailyLimitReached,
    BrainError,
    BrainValidationError,
)
from ..brain.filters import platform_midnight
from ..brain.schemas import SimulationStatus
from ..db.models import ACTIVE, QuotaSnapshot, SimStatus, SimulationRecord, utcnow
from ..tasks import spawn
from . import reconcile
from .lifecycle import (
    SIMULATION_COST,
    Outcome,
    SubmissionFailed,
    cancel_after_lost_race,
    describe_fields,
    extract_simulation_id,
    read_outcome,
    record_launch,
    remember,
    serialise,
    transition,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from ..brain.endpoints import BrainEndpoints
    from ..brain.schemas import SimulationRequest
    from ..db.sqlite import Database

log = structlog.get_logger(__name__)

#: How often the poll loop wakes to look for work.
TICK_SECONDS = 1.0
#: Fallback interval when the platform gives us no Retry-After.
DEFAULT_POLL_SECONDS = 3.0
#: A PENDING row older than this crashed mid-submit and needs attention.
PENDING_GRACE_SECONDS = 120.0
#: How often stale sends are looked for after startup.
SWEEP_SECONDS = 60.0
#: A batch whose reported progress does not move for this long has stopped being useful.
#: BRAIN normally advances a batch within minutes; two hours leaves plenty of room for a
#: slow market while preventing an interrupted machine from losing every local core forever.
STALLED_BATCH_AGE = timedelta(hours=2)


class SimulationTracker:
    """Submits simulations, keeps their ids on disk, and polls them to completion."""

    def __init__(
        self,
        db: Database,
        endpoints: BrainEndpoints,
        *,
        on_change: Callable[[list[dict[str, Any]]], Awaitable[None]],
        on_unauthorized: Callable[[], Awaitable[bool]],
        on_alpha: Callable[[str], Awaitable[None]],
        on_stalled: Callable[[str], Awaitable[None]],
    ) -> None:
        self.db = db
        self.endpoints = endpoints
        self._on_change = on_change
        #: Asked when a poll is refused for the session, so an expired one is renewed
        #: instead of every running simulation backing off forever.
        self._on_unauthorized = on_unauthorized
        #: Called with each new alpha id as it lands, so the vault can capture the alpha
        #: and its daily returns without anyone asking. Failures inside it never affect the
        #: simulation that produced the alpha.
        self._on_alpha = on_alpha
        #: Pauses the owning lab task and removes work it has not sent before a stalled
        #: BRAIN batch is closed locally.
        self._on_stalled = on_stalled
        self._task: asyncio.Task[None] | None = None
        # record_id -> monotonic time of the next allowed poll. In memory only; losing
        # it just means we poll once immediately after a restart.
        self._next_poll: dict[int, float] = {}
        #: Adopted rows this process is sending right now. Their ``created_at`` is when
        #: they were queued, so without this the stale-send check could orphan one
        #: mid-request.
        self.sending: set[int] = set()
        self._last_sweep = time.monotonic()
        #: The orphan pass, run beside the poll loop so polling never waits on a listing.
        self._reconcile: asyncio.Task[dict[str, int] | None] | None = None

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="simulation-tracker")
        log.info("tracker.started")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        log.info("tracker.stopped")

    # -- submission ------------------------------------------------------

    async def submit(self, request: SimulationRequest, record_id: int) -> None:
        """Start one queued simulation, recording its id durably. See :meth:`_submit`."""
        self.sending.add(record_id)
        try:
            await self._submit(request, record_id)
        finally:
            self.sending.discard(record_id)

    async def _submit(self, request: SimulationRequest, record_id: int) -> None:
        """Start one simulation, recording its id durably.

        Ordering is deliberate and must not be rearranged:

        1. move the queued row to ``PENDING`` before touching the network;
        2. issue the POST;
        3. commit the platform id from ``Location`` and flip to ``RUNNING``.

        A failure at step 2 marks the row ``REJECTED``, or hands it back to the queue when
        the failure says nothing about the alpha. A crash between 2 and 3 leaves it
        ``PENDING``, which :meth:`reconcile` reports.
        """
        # --- 1. persist intent before touching the network ---------------
        async with self.db.session() as session:
            won = await transition(
                session,
                record_id,
                from_=[SimStatus.QUEUED],
                status=SimStatus.PENDING,
                sent_at=utcnow(),
            )
        if not won:
            raise SubmissionFailed(
                "No longer queued; it was removed before it was sent.", record_id=record_id
            )

        log.info("sim.pending", record_id=record_id, region=request.settings.region)
        await self.notify()

        # --- 2. the network call ----------------------------------------
        try:
            response = await self.endpoints.create_simulation(request)
        except BrainValidationError as exc:
            # Pass the platform's own wording through untouched — "TOP9000 is not a
            # valid choice" tells the researcher what to fix; "rejected" does not.
            reason = describe_fields(exc.fields, exc.message)
            await self._mark(
                record_id, SimStatus.REJECTED, message=reason, from_=[SimStatus.PENDING]
            )
            raise SubmissionFailed(
                f"BRAIN rejected these settings — {reason}",
                record_id=record_id,
                cause=exc,
                fields=exc.fields,
            ) from exc
        except BrainError as exc:
            if exc.maybe_delivered:
                # The POST may be running on BRAIN with its id lost in transit. Resending
                # now would pay for it twice; reconciliation adopts the alpha it produces
                # or queues the request again once it is clear none is coming.
                await self._mark(
                    record_id,
                    SimStatus.ORPHANED,
                    message=(
                        f"BRAIN may have started this simulation but its answer was lost "
                        f"({exc.message}). Matching it against your alphas."
                    ),
                    finished=False,
                    from_=[SimStatus.PENDING],
                )
            elif exc.retryable or isinstance(exc, BrainDailyLimitReached | BrainAuthError):
                # A throttle, an outage or the daily cap says nothing about the alpha.
                # Back in the queue, so it runs once the platform will take it.
                await self._mark(
                    record_id,
                    SimStatus.QUEUED,
                    message=exc.message,
                    finished=False,
                    from_=[SimStatus.PENDING],
                )
            else:
                await self._mark(
                    record_id, SimStatus.REJECTED, message=exc.message, from_=[SimStatus.PENDING]
                )
            raise SubmissionFailed(exc.message, record_id=record_id, cause=exc) from exc

        # --- 3. capture the id immediately ------------------------------
        platform_id = extract_simulation_id(response.location)
        if platform_id is None:
            # A 201 with no Location. We cannot cancel what we cannot name — say so
            # loudly rather than pretending the submission failed.
            await self._mark(
                record_id,
                SimStatus.ORPHANED,
                message=(
                    "BRAIN accepted the simulation but returned no Location header, so "
                    "its id is unknown and it cannot be cancelled from here. Check the "
                    "platform directly."
                ),
                from_=[SimStatus.PENDING],
            )
            log.error("sim.no_location", record_id=record_id, status=response.status)
            raise SubmissionFailed(
                "Simulation started but its id was not returned; it cannot be cancelled.",
                record_id=record_id,
            )

        async with self.db.session() as session:
            won = await record_launch(session, record_id, platform_id, response.rate_limit)

        # Our own simulation, started against the user's wishes: stop it on BRAIN. If BRAIN
        # will not, it is running after all and is polled like any other.
        if not won and await cancel_after_lost_race(
            self.db, self.endpoints, record_id, platform_id
        ):
            log.info("sim.cancelled_after_send", record_id=record_id, platform_id=platform_id)
            await self.notify()
            raise SubmissionFailed("Cancelled while it was being sent.", record_id=record_id)

        log.info("sim.running", record_id=record_id, platform_id=platform_id)
        self.watch(record_id, response.retry_after)
        await self.notify()

    def watch(self, record_id: int, delay: float | None = None) -> None:
        """Start polling a record after ``delay``, the 201's Retry-After if it sent one.

        Used by the batch engine after it records a parent id, so a freshly submitted
        batch is picked up without waiting for a sweep.
        """
        self._next_poll[record_id] = time.monotonic() + (delay or 0.0)

    # -- cancellation ----------------------------------------------------

    async def cancel(self, record_id: int) -> bool:
        """Cancel a simulation and mark it locally.

        Returns ``False`` when there is nothing to cancel — no platform id yet, or the
        row already reached a terminal state.
        """
        record = await self.get(record_id)
        if record is None:
            return False
        if SimStatus(record.status).terminal:
            return False
        if record.parent_record_id is not None and record.status != SimStatus.QUEUED:
            # A batch child has no handle of its own; only the parent can be cancelled,
            # and that would take its siblings with it.
            return False
        if not record.platform_id:
            # Queued or mid-submit. The send's own id write loses to this mark and then
            # cancels the simulation on BRAIN itself.
            await self._mark(
                record_id,
                SimStatus.CANCELLED,
                message="Cancelled before BRAIN returned an id.",
            )
            return False

        ok = await self.endpoints.cancel_simulation(record.platform_id)
        log.info("sim.cancelled", record_id=record_id, platform_id=record.platform_id, ok=ok)
        if not ok:
            # Refused: it already finished, or BRAIN lost it. Marking it CANCELLED would
            # hide a result that exists, so the next poll records what really happened.
            self.watch(record_id)
            return False
        await self._mark(record_id, SimStatus.CANCELLED, finished=True)
        self._next_poll.pop(record_id, None)
        return ok

    async def force_close(
        self,
        record_ids: Iterable[int],
        *,
        message: str,
        status: SimStatus = SimStatus.CANCELLED,
    ) -> int:
        """Stop tracking active records after an explicit forced task stop.

        BRAIN can refuse ``DELETE`` for a batch it has lost or can no longer inspect. A forced
        stop is the user's instruction to release the local cores anyway. Shared batch parents
        are excluded by the caller so closing these rows cannot interrupt another task.
        """
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            return 0
        async with self.db.session() as session:
            result = await session.execute(
                update(SimulationRecord)
                .where(
                    SimulationRecord.id.in_(ids),
                    SimulationRecord.status.in_(ACTIVE),
                )
                .values(
                    status=status,
                    finished_at=utcnow(),
                    message=message,
                )
            )
            closed = result.rowcount or 0  # pyright: ignore[reportAttributeAccessIssue]
        for record_id in ids:
            self._next_poll.pop(record_id, None)
        if closed:
            log.warning("sim.force_closed", count=closed)
            await self.notify()
        return closed

    # -- reads -----------------------------------------------------------

    async def get(self, record_id: int) -> SimulationRecord | None:
        async with self.db.session() as session:
            return await session.get(SimulationRecord, record_id)

    async def active(self) -> list[SimulationRecord]:
        """What is pending or running on BRAIN: at most the slots and their children.

        Queued work is left out on purpose: a harvest can queue twenty thousand rows, and
        reading and pushing those every tick would scale the hot path with the backlog
        instead of the slots.
        """
        async with self.db.session() as session:
            return list(
                await session.scalars(
                    select(SimulationRecord)
                    .where(SimulationRecord.status.in_([SimStatus.PENDING, SimStatus.RUNNING]))
                    .order_by(SimulationRecord.created_at)
                )
            )

    async def used_today(self) -> int:
        """Simulations consumed since the platform's day began.

        Counted locally because nothing exposes the real figure until a simulation POST
        comes back with its headers, and the Today page needs a number before the first one
        runs. Batch *parents* are excluded and their children counted instead, since the
        platform charges for every child; rows count on the day they were actually sent.
        A region-agnostic row counts four, the most regions it can be translated into.
        """
        start = platform_midnight()
        async with self.db.session() as session:
            total = await session.scalar(
                select(func.sum(SIMULATION_COST))
                .select_from(SimulationRecord)
                .where(
                    SimulationRecord.is_batch.is_(False),
                    SimulationRecord.submitted_at >= start.astimezone(UTC),
                )
            )
        return int(total or 0)

    async def latest_quota(self) -> QuotaSnapshot | None:
        async with self.db.session() as session:
            return await session.scalar(
                select(QuotaSnapshot).order_by(QuotaSnapshot.observed_at.desc()).limit(1)
            )

    # -- recovery --------------------------------------------------------

    async def reconcile(self) -> dict[str, int]:
        """Re-adopt in-flight simulations after a restart.

        ``RUNNING`` rows have a platform id, so polling simply resumes — that is the
        common case and it is fully recoverable.

        ``PENDING`` rows are the crash window: we sent a POST but never learned the id.
        Once stale they are marked ``ORPHANED``, and :meth:`reconcile_orphans` matches
        them to the alpha they produced, or queues them again when none appears.
        """
        resumed = 0
        async with self.db.session() as session:
            running = await session.scalars(
                select(SimulationRecord.id).where(
                    SimulationRecord.status == SimStatus.RUNNING,
                    SimulationRecord.platform_id.is_not(None),
                )
            )
            for record_id in running:
                self._next_poll[record_id] = time.monotonic()
                resumed += 1

        # A batch's RUNNING children carry no id of their own until the parent is read
        # back. The parent's id is the handle, so they are left for the engine to expand.
        orphaned = await self.orphan_stale()

        if resumed or orphaned:
            log.warning("tracker.reconciled", resumed=resumed, orphaned=orphaned)
            await self.notify()
        return {"resumed": resumed, "orphaned": orphaned}

    async def orphan_stale(self) -> int:
        """Flag sends that never learned their id, so they stop holding a slot.

        Runs at startup and then on a timer, because a send cut off by a restart can be
        seconds old when the next process starts and would otherwise stay counted as in
        flight until the restart after. Judged by when the send began — a batch's children
        go by their parent — and anything this process is sending right now is left alone.
        """
        cutoff = time.time() - PENDING_GRACE_SECONDS
        async with self.db.session() as session:
            pending = list(
                await session.scalars(
                    select(SimulationRecord).where(SimulationRecord.status == SimStatus.PENDING)
                )
            )
            parents = {r.id: r for r in pending}
            stale: list[int] = []
            for record in pending:
                if record.id in self.sending or record.parent_record_id in self.sending:
                    continue
                anchor = record
                if record.parent_record_id is not None:
                    anchor = (
                        parents.get(record.parent_record_id)
                        or await session.get(SimulationRecord, record.parent_record_id)
                        or record
                    )
                if anchor.created_at.timestamp() < cutoff:
                    stale.append(record.id)
            orphaned = len(
                await transition(
                    session,
                    stale,
                    from_=[SimStatus.PENDING],
                    status=SimStatus.ORPHANED,
                    message=(
                        "The backend stopped between sending this simulation and "
                        "recording its id. Matching it against your alphas."
                    ),
                    finished_at=utcnow(),
                )
            )

        if orphaned:
            log.warning("tracker.orphaned", count=orphaned)
            await self.notify()
        return orphaned

    async def reconcile_orphans(self) -> dict[str, int]:
        """Adopt or requeue sends whose answer was lost. See :mod:`.reconcile`."""
        result = await reconcile.reconcile_orphans(
            self.db, self.endpoints, on_alpha=self.alpha_landed
        )
        if any(result.values()):
            await self.notify()
        return result

    # -- polling ---------------------------------------------------------

    async def _run(self) -> None:
        """Poll every active simulation, honouring each one's Retry-After."""
        while True:
            try:
                await self._tick()
                if time.monotonic() - self._last_sweep >= SWEEP_SECONDS:
                    self._last_sweep = time.monotonic()
                    await self.orphan_stale()
                    # A failed pass is retried on the next sweep.
                    if self._reconcile is None or self._reconcile.done():
                        self._reconcile = spawn(self.reconcile_orphans(), name="orphan-reconcile")
            except Exception:
                log.exception("tracker.tick_failed")
            await asyncio.sleep(TICK_SECONDS)

    async def _tick(self) -> None:
        records = await self.active()
        if not records:
            return

        now = time.monotonic()
        changed = False
        for record in records:
            if not record.platform_id:
                continue
            if record.is_batch and record.child_ids:
                # Fanned out: the engine polls its children and finishes it.
                continue
            if now < self._next_poll.get(record.id, 0.0):
                continue
            if await self._poll_one(record):
                changed = True

        if changed:
            await self.notify()

    async def _poll_one(self, record: SimulationRecord) -> bool:
        """One status read. Returns True if anything changed."""
        if record.platform_id is None:
            raise ValueError(f"simulation record {record.id} has no platform id to poll")
        try:
            response = await self.endpoints.read_simulation(record.platform_id)
        except BrainError as exc:
            # Transport hiccups are expected; back off and try again rather than
            # declaring a running simulation dead.
            log.warning("sim.poll_failed", record_id=record.id, error=str(exc))
            self._next_poll[record.id] = time.monotonic() + DEFAULT_POLL_SECONDS
            return False

        outcome = read_outcome(response.status, response.body, response.retry_after)
        match outcome.kind:
            case "unauthorized":
                log.warning("sim.poll_unauthenticated", record_id=record.id)
                self._next_poll[record.id] = time.monotonic() + 10.0
                await self._on_unauthorized()
                return False
            case "retry":
                # A 403, 429 or 5xx is not a result: treating it as one would close a
                # running simulation with no alpha and stop polling it.
                if await self._close_if_stalled(record):
                    return True
                log.warning("sim.poll_error", record_id=record.id, status=response.status)
                self._next_poll[record.id] = time.monotonic() + (outcome.delay or 15.0)
                return False
            case "pending":
                if outcome.progress is None or outcome.progress == record.progress:
                    if await self._close_if_stalled(record):
                        return True
                    self._next_poll[record.id] = time.monotonic() + (
                        outcome.delay or DEFAULT_POLL_SECONDS
                    )
                    return False
                async with self.db.session() as session:
                    await session.execute(
                        update(SimulationRecord)
                        .where(SimulationRecord.id == record.id)
                        .values(progress=outcome.progress, last_polled_at=utcnow())
                    )
                self._next_poll[record.id] = time.monotonic() + (
                    outcome.delay or DEFAULT_POLL_SECONDS
                )
                return True
            case "fanout":
                return await self._hand_over_children(record, outcome)
            case "gone" | "finished":
                return await self._apply_terminal(record, outcome)

    async def _close_if_stalled(self, record: SimulationRecord) -> bool:
        """Pause and release a batch BRAIN has left unchanged for two hours.

        ``last_polled_at`` is the last time progress changed. Older rows predate that use,
        so their submission time is the conservative fallback. A current BRAIN response is
        required before reaching this method; time spent offline alone never closes work.
        """
        if not record.is_batch or record.parent_record_id is not None:
            return False
        anchor = record.last_polled_at or record.submitted_at or record.created_at
        if utcnow() - anchor < STALLED_BATCH_AGE:
            return False

        await self._on_stalled(record.task)
        acknowledged = False
        try:
            acknowledged = await self.endpoints.cancel_simulation(record.platform_id or "")
        except BrainError as exc:
            log.warning(
                "sim.stalled_cancel_failed",
                record_id=record.id,
                platform_id=record.platform_id,
                error=str(exc),
            )

        async with self.db.session() as session:
            children = list(
                (
                    await session.scalars(
                        select(SimulationRecord.id).where(
                            SimulationRecord.parent_record_id == record.id,
                            SimulationRecord.status.in_(ACTIVE),
                        )
                    )
                ).all()
            )
        message = (
            "BRAIN reported no progress for two hours. The batch was closed locally and "
            "its task was paused; Sync with BRAIN can recover results that arrive later."
        )
        closed = await self.force_close(
            [record.id, *children], message=message, status=SimStatus.TIMEOUT
        )
        log.warning(
            "sim.stalled_closed",
            record_id=record.id,
            platform_id=record.platform_id,
            cancelled=acknowledged,
            closed=closed,
        )
        return bool(closed)

    async def _hand_over_children(self, record: SimulationRecord, outcome: Outcome) -> bool:
        """A multi-simulation has dispatched its children: the engine reads them from here.

        A parent that says COMPLETE is done on BRAIN, children included, and finishes now.
        A bare ``{children}`` — the documented form while children may still run — keeps
        the parent RUNNING, because its children occupy its slot on BRAIN and freeing it
        would draw a concurrency 429. Either way polling stops for good, restarts included,
        because ``child_ids`` is on disk.
        """
        self._next_poll.pop(record.id, None)
        done = outcome.platform_status == SimulationStatus.COMPLETE
        finished: dict[str, Any] = (
            {"status": SimStatus.COMPLETE, "finished_at": utcnow()} if done else {}
        )
        async with self.db.session() as session:
            won = await transition(
                session,
                record.id,
                from_=[SimStatus.RUNNING],
                child_ids=list(outcome.children),
                is_batch=True,
                platform_status=str(outcome.platform_status) if outcome.platform_status else None,
                progress=1.0,
                **finished,
            )
        log.info("sim.fanned_out", record_id=record.id, children=len(outcome.children))
        return bool(won)

    async def _apply_terminal(self, record: SimulationRecord, outcome: Outcome) -> bool:
        """Write a finished simulation's outcome."""
        alpha_id = outcome.alpha_id
        async with self.db.session() as session:
            won = await transition(
                session,
                record.id,
                from_=ACTIVE,
                status=outcome.status,
                platform_status=str(outcome.platform_status) if outcome.platform_status else None,
                alpha_id=alpha_id,
                message=outcome.message,
                progress=1.0 if outcome.status == SimStatus.COMPLETE else record.progress,
                finished_at=utcnow(),
            )

            # Without this, re-running an identical alpha spends daily quota to recreate
            # something that already exists. Batch parents are excluded: their payload is
            # the array, not an alpha.
            if won and alpha_id and not record.is_batch:
                await remember(session, record, alpha_id)

        self._next_poll.pop(record.id, None)
        log.info("sim.finished", record_id=record.id, status=str(outcome.status), alpha_id=alpha_id)
        if alpha_id:
            self.alpha_landed(alpha_id)
        return True

    def alpha_landed(self, alpha_id: str) -> None:
        """Hand a finished alpha to ``on_alpha`` without waiting on it.

        Fire and forget: capturing an alpha's returns is a convenience for later analysis,
        not something a finished simulation should wait on or fail with. Public because a
        batch's children are resolved by the engine and need exactly the same hand-off.
        """
        spawn(self._on_alpha(alpha_id), name=f"capture-{alpha_id}")

    # -- helpers ---------------------------------------------------------

    async def _mark(
        self,
        record_id: int,
        status: SimStatus,
        *,
        message: str | None = None,
        finished: bool = True,
        from_: Iterable[SimStatus] = ACTIVE,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if message is not None:
            values["message"] = message
        if finished:
            values["finished_at"] = utcnow()
        async with self.db.session() as session:
            await transition(session, record_id, from_=from_, **values)
        await self.notify()

    async def notify(self) -> None:
        """Push what is pending or running now; the batch engine announces through this too."""
        try:
            await self._on_change([serialise(r) for r in await self.active()])
        except Exception:
            log.exception("tracker.notify_failed")
