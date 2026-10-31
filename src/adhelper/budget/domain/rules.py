from dataclasses import dataclass, fields

BASIS_POINTS = 10_000


def require_int(**values: object) -> None:
    """Деньги и доли только целые: bool и float здесь всегда ошибка."""
    for name, value in values.items():
        if type(value) is not int:
            raise TypeError(f"{name} должно быть целым, получено {type(value).__name__}")


def floor_share(amount: int, share_bp: int) -> int:
    return amount * share_bp // BASIS_POINTS


def ceil_share(amount: int, share_bp: int) -> int:
    return -(-amount * share_bp // BASIS_POINTS)


@dataclass(frozen=True)
class BudgetRules:
    version: str
    reserve_bp: int
    stop_threshold_bp: int
    alert_threshold_bp: int
    change_spike_old_bp: int
    change_spike_new_bp: int
    max_increase_bp: int
    min_increase_interval_days: int
    check_interval_seconds: int
    alert_check_interval_seconds: int
    stop_latency_seconds: int

    def __post_init__(self) -> None:
        require_int(**{f.name: getattr(self, f.name) for f in fields(self) if f.name != "version"})
        if self.alert_threshold_bp > self.stop_threshold_bp:
            raise ValueError("Порог предупреждения выше порога остановки")
