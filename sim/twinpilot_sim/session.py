"""SimSession: operator command -> Safety Layer -> simulator, one fixed step at a time."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from twinpilot_sim.adapter import GroundTruth
from twinpilot_sim.control.safety import RiskLevel, SafetyDecision, SafetyLayer
from twinpilot_sim.faults import FaultInjector
from twinpilot_sim.operator import OperatorConfig, ScriptedOperator
from twinpilot_sim.params import ExcavatorParams, default_params
from twinpilot_sim.schema import ControlCommand, FaultSpec, TelemetryRecord
from twinpilot_sim.simulator import PythonSimulator, offline_clock
from twinpilot_sim.world import TaskConfig, WorldEvent, load_task


@dataclass(frozen=True)
class SafetyEvent:
    time: float
    level: str
    rules: tuple[str, ...]


@dataclass
class StepResult:
    records: list[TelemetryRecord]
    decision: SafetyDecision
    events: list[WorldEvent]
    safety_events: list[SafetyEvent] = field(default_factory=list)


class SimSession:
    def __init__(
        self,
        run_id: str,
        seed: int = 0,
        faults: list[FaultSpec] | None = None,
        params: ExcavatorParams | None = None,
        task: TaskConfig | None = None,
        clock: Callable[[float], int] = offline_clock,
        payload_scale: float = 1.0,
    ):
        self.params = params or default_params()
        self.task = task or load_task()
        self.faults = FaultInjector(faults or [], seed)
        self.sim = PythonSimulator(
            run_id=run_id,
            seed=seed,
            params=self.params,
            task=self.task,
            faults=self.faults,
            clock=clock,
            payload_scale=payload_scale,
        )
        self.safety = SafetyLayer(self.params, self.task)
        self.seed = seed
        self.run_id = run_id
        self._gt = self.sim.ground_truth()
        self._last_rules: tuple[str, ...] = ()
        self.safety_log: list[SafetyEvent] = []
        self.event_log: list[WorldEvent] = []
        self.command_changes = 0.0
        self.direction_reversals = 0
        self._prev_axes: dict[str, float] | None = None

    @property
    def sim_time(self) -> float:
        return self.sim.sim_time

    @property
    def dt(self) -> float:
        return self.sim.dt

    def ground_truth(self) -> GroundTruth:
        return self._gt

    def inject(self, fault: FaultSpec) -> None:
        self.faults.add(fault)

    def submit(self, cmd: ControlCommand) -> bool:
        return self.safety.receive(cmd, self.sim_time)

    def step(self) -> StepResult:
        decision = self.safety.evaluate(self.sim_time, self._gt, self.dt)
        axes = decision.command.axes()
        if self._prev_axes is not None:
            for a, v in axes.items():
                pv = self._prev_axes[a]
                self.command_changes += abs(v - pv)
                if v * pv < 0:
                    self.direction_reversals += 1
        self._prev_axes = axes
        records = self.sim.step(decision.command)
        self._gt = self.sim.ground_truth()
        self.event_log.extend(self._gt.events)
        safety_events: list[SafetyEvent] = []
        notable = tuple(r for r in decision.rules if r != "rate_limited")
        if notable != self._last_rules and notable and decision.level >= RiskLevel.CAUTION:
            ev = SafetyEvent(self.sim_time, decision.level.name, notable)
            safety_events.append(ev)
            self.safety_log.append(ev)
        self._last_rules = notable
        return StepResult(records, decision, self._gt.events, safety_events)

    def metrics(self) -> dict[str, float | int | bool]:
        c = self.sim.world.counters
        steps = max(1, self.sim.tick)
        done = c.completed_at is not None
        return {
            "sim_time": round(self.sim_time, 3),
            "delivered": c.delivered,
            "target_count": self.task.target_count,
            "completed": done,
            "completion_time": round(c.completed_at, 3) if done else -1.0,
            "within_time_limit": done and c.completed_at <= self.task.time_limit_s,
            "collisions": c.collisions,
            "worker_collisions": c.worker_collisions,
            "worker_proximity_entries": c.worker_proximity,
            "forbidden_entries": c.forbidden_entries,
            "drops": c.drops,
            "tipover_risks": c.tipover_risks,
            "safety_events": len(self.safety_log),
            "direction_reversals": self.direction_reversals,
            "mean_command_change": round(self.command_changes / steps, 5),
            "energy_kj": round(self.sim.energy_kj, 2),
        }


def make_operator(session: SimSession, config: OperatorConfig | None = None) -> ScriptedOperator:
    return ScriptedOperator(session.params, session.task, session.seed, config)
