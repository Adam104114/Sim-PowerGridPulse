# core/controllers.py
from dataclasses import dataclass
import math
import random
from core.generators import ThermalGenerator

@dataclass(frozen=True)
class AGCConfig:
    bess_kp: float = 55.0
    bess_kd: float = 18.0
    bess_deadband_hz: float = 0.020
    bess_target_soc: float = 0.65
    bess_soc_recovery_gain: float = 25.0

    agc_kp: float = 65.0
    agc_ki: float = 20.0
    integral_limit: float = 400.0
    deadband_hz: float = 0.005


class EconomicDispatcher:
    """
    Merit Order (Marjinal Maliyet Sıralaması) tabanlı Ekonomik Yük Dağıtıcı.
    Aktif (arıza yapmamış) santralleri en ucuz $/MWh maliyetinden en pahalıya sıralar
    ve toplam termik güç ihtiyacını minimum maliyetle paylaştırır.
    """
    @staticmethod
    def allocate_power(total_required_mw: float, generators: list[ThermalGenerator]) -> None:
        # 1. Sadece devrede olan (sağlam) santralleri maliyete göre sırala
        available_gens = sorted(
            [g for g in generators if not g.is_tripped],
            key=lambda g: g.config.marginal_cost_usd_mwh
        )

        # Arızalı santrallerin hedefini sıfırla
        for g in generators:
            if g.is_tripped:
                g.set_agc_dispatch(0.0)

        if not available_gens:
            return

        # 2. Önce her sağlam santrale teknik minimum yükünü (min_power_mw) tahsis et
        remaining_mw = total_required_mw
        allocations = {}
        for g in available_gens:
            base_alloc = min(g.config.min_power_mw, remaining_mw)
            allocations[g.name] = base_alloc
            remaining_mw -= base_alloc

        # 3. Kalan güç ihtiyacını en ucuz santralden başlayarak kapasite sınırına kadar dağıt
        for g in available_gens:
            if remaining_mw <= 0:
                break
            headroom_mw = g.config.rated_power_mw - allocations[g.name]
            added_mw = min(headroom_mw, remaining_mw)
            allocations[g.name] += added_mw
            remaining_mw -= added_mw

        # 4. Hesaplanan hedefleri santrallere gönder
        for g in available_gens:
            g.set_agc_dispatch(allocations[g.name])


class AGCController:
    """Çoklu Santral ve BESS destekli Hiyerarşik AGC Kontrolcüsü."""
    def __init__(self, config: AGCConfig, base_thermal_dispatch_mw: float = 260.0):
        self.config = config
        self._integral_error: float = 0.0
        self._base_dispatch_mw: float = base_thermal_dispatch_mw

    def compute_dispatch(
        self,
        dt: float,
        current_freq_hz: float,
        nominal_freq_hz: float,
        rocof_hz_s: float,
        bess_soc: float,
        total_thermal_min_mw: float,
        total_thermal_max_mw: float
    ) -> tuple[float, float]:
        raw_error = nominal_freq_hz - current_freq_hz
        freq_error = 0.0 if abs(raw_error) < self.config.deadband_hz else raw_error

        # 1. HIZLI KATMAN: BESS FFR + Sanal Atalet
        if abs(raw_error) > self.config.bess_deadband_hz:
            effective_bess_error = raw_error - math.copysign(self.config.bess_deadband_hz, raw_error)
            ffr_power = (self.config.bess_kp * effective_bess_error) - (self.config.bess_kd * rocof_hz_s)
            bess_command_mw = ffr_power
        else:
            ffr_power = 0.0
            soc_deviation = self.config.bess_target_soc - bess_soc
            if abs(raw_error) < 0.015 and abs(soc_deviation) > 0.01:
                bess_command_mw = max(-15.0, min(15.0, -self.config.bess_soc_recovery_gain * soc_deviation))
            else:
                bess_command_mw = 0.0

        # 2. YAVAŞ KATMAN: Toplam Termik Üretim İhtiyacı (PI + Anti-Windup)
        ace_signal = freq_error + (ffr_power * 0.008)

        candidate_integral = self._integral_error + (ace_signal * dt)
        candidate_integral = max(
            -self.config.integral_limit / max(self.config.agc_ki, 1e-6),
            min(self.config.integral_limit / max(self.config.agc_ki, 1e-6), candidate_integral)
        )

        total_thermal_target = (
            self._base_dispatch_mw
            + (self.config.agc_kp * ace_signal)
            + (self.config.agc_ki * candidate_integral)
        )

        if total_thermal_min_mw <= total_thermal_target <= total_thermal_max_mw:
            self._integral_error = candidate_integral
        else:
            total_thermal_target = max(total_thermal_min_mw, min(total_thermal_max_mw, total_thermal_target))

        return total_thermal_target, bess_command_mw


class CityLoadProfile:
    """24 saatlik şehir elektrik tüketim (MW) simülatörü."""
    def __init__(self, base_load_mw: float = 280.0, seed: int = 42):
        self.base_load_mw = base_load_mw
        self._rng = random.Random(seed)
        self._current_noise = 0.0

    def get_demand_mw(self, sim_time_s: float, dt: float) -> float:
        hour = (sim_time_s / 3600.0) % 24.0
        daytime_curve = 70.0 * math.sin((hour - 6.0) * math.pi / 12.0)
        evening_peak = 85.0 * math.exp(-((hour - 20.0) ** 2) / 3.5)

        self._current_noise += self._rng.uniform(-0.8, 0.8) * math.sqrt(dt)
        self._current_noise = max(-12.0, min(12.0, self._current_noise))

        industrial_shock = 65.0 if (14.0 <= hour <= 14.5) else 0.0
        return max(150.0, self.base_load_mw + daytime_curve + evening_peak + self._current_noise + industrial_shock)