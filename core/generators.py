# core/generators.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
import math
import random

class PowerSource(ABC):
    """Tüm enerji üretim ve depolama birimleri için soyut temel sınıf."""
    def __init__(self, name: str, rated_power_mw: float):
        self.name = name
        self.rated_power_mw = rated_power_mw
        self.current_output_mw: float = 0.0
        self.is_tripped: bool = False

    def trip(self) -> None:
        """N-1 Arıza durumu: Ünite koruma rölesiyle şebekeden ayrılır."""
        self.is_tripped = True
        self.current_output_mw = 0.0

    @abstractmethod
    def step(self, dt: float, grid_freq_hz: float, nominal_freq_hz: float, **kwargs) -> float:
        pass


@dataclass(frozen=True)
class ThermalConfig:
    rated_power_mw: float
    min_power_mw: float
    turbine_time_const_s: float
    max_ramp_rate_mw_s: float
    marginal_cost_usd_mwh: float   # Marjinal üretim maliyeti ($/MWh)
    droop_r: float = 0.05


class ThermalGenerator(PowerSource):
    """
    Maliyet odaklı (Economic Dispatch uyumlu) ve arıza (N-1 Trip) simülasyonlu
    senkron termik santral modeli.
    """
    def __init__(self, name: str, config: ThermalConfig, initial_output_mw: float):
        super().__init__(name, config.rated_power_mw)
        self.config = config
        self.current_output_mw = initial_output_mw
        self.agc_setpoint_mw = initial_output_mw
        self.total_cost_usd: float = 0.0

    def set_agc_dispatch(self, target_mw: float) -> None:
        if self.is_tripped:
            self.agc_setpoint_mw = 0.0
            return
        self.agc_setpoint_mw = max(
            self.config.min_power_mw,
            min(self.config.rated_power_mw, target_mw)
        )

    def step(self, dt: float, grid_freq_hz: float, nominal_freq_hz: float, **kwargs) -> float:
        if self.is_tripped:
            self.current_output_mw = 0.0
            return 0.0

        # 1. Primary Droop Tepkisi
        freq_deviation_pu = (grid_freq_hz - nominal_freq_hz) / nominal_freq_hz
        droop_response_mw = -(1.0 / self.config.droop_r) * freq_deviation_pu * self.config.rated_power_mw

        # 2. Toplam mekanik valf komutu
        total_command_mw = max(
            self.config.min_power_mw,
            min(self.config.rated_power_mw, self.agc_setpoint_mw + droop_response_mw)
        )

        # 3. Birinci Dereceden Türbin Gecikmesi (First-Order Lag)
        raw_rate = (total_command_mw - self.current_output_mw) / self.config.turbine_time_const_s

        # 4. Fiziksel Ramp-Rate Sınırlaması
        clamped_rate = max(
            -self.config.max_ramp_rate_mw_s,
            min(self.config.max_ramp_rate_mw_s, raw_rate)
        )

        self.current_output_mw += clamped_rate * dt

        # 5. Kümülatif Yakıt Maliyeti Entegrasyonu ($ = MW * (s/3600) * $/MWh)
        self.total_cost_usd += self.current_output_mw * (dt / 3600.0) * self.config.marginal_cost_usd_mwh
        return self.current_output_mw


@dataclass(frozen=True)
class BESSConfig:
    rated_power_mw: float = 120.0
    capacity_mwh: float = 240.0
    min_soc: float = 0.10
    max_soc: float = 0.95
    efficiency: float = 0.92
    inverter_lag_s: float = 0.2


class BatteryStorageSystem(PowerSource):
    """Şebeke ölçekli Batarya Enerji Depolama Sistemi (BESS)."""
    def __init__(self, name: str, config: BESSConfig, initial_soc: float = 0.65):
        super().__init__(name, config.rated_power_mw)
        self.config = config
        self.soc = initial_soc
        self.power_command_mw: float = 0.0

    def set_power_command(self, target_mw: float) -> None:
        self.power_command_mw = max(
            -self.config.rated_power_mw,
            min(self.config.rated_power_mw, target_mw)
        )

    def step(self, dt: float, grid_freq_hz: float, nominal_freq_hz: float, **kwargs) -> float:
        effective_command = self.power_command_mw
        if effective_command > 0 and self.soc <= self.config.min_soc:
            effective_command = 0.0
        elif effective_command < 0 and self.soc >= self.config.max_soc:
            effective_command = 0.0

        alpha = min(1.0, dt / self.config.inverter_lag_s)
        self.current_output_mw += alpha * (effective_command - self.current_output_mw)

        dt_hours = dt / 3600.0
        if self.current_output_mw > 0:
            energy_drawn_mwh = (self.current_output_mw / self.config.efficiency) * dt_hours
            self.soc -= energy_drawn_mwh / self.config.capacity_mwh
        elif self.current_output_mw < 0:
            energy_stored_mwh = (abs(self.current_output_mw) * self.config.efficiency) * dt_hours
            self.soc += energy_stored_mwh / self.config.capacity_mwh

        self.soc = max(0.0, min(1.0, self.soc))
        return self.current_output_mw


class SolarFarm(PowerSource):
    """Kontrol edilemeyen Güneş Enerjisi Santrali (GES)."""
    def __init__(self, name: str, rated_power_mw: float, seed: int = 42):
        super().__init__(name, rated_power_mw)
        self._rng = random.Random(seed)
        self._cloud_cover = 0.0

    def step(self, dt: float, grid_freq_hz: float, nominal_freq_hz: float, **kwargs) -> float:
        sim_time_s: float = kwargs.get("sim_time_s", 0.0)
        hour_of_day = (sim_time_s / 3600.0) % 24.0

        if not (6.0 <= hour_of_day <= 18.0):
            self.current_output_mw = 0.0
            return 0.0

        clear_sky_factor = math.sin((hour_of_day - 6.0) * math.pi / 12.0)
        cloud_delta = self._rng.uniform(-0.02, 0.02) * math.sqrt(dt)
        self._cloud_cover = max(0.0, min(0.75, self._cloud_cover + cloud_delta))

        self.current_output_mw = self.rated_power_mw * clear_sky_factor * (1.0 - self._cloud_cover)
        return self.current_output_mw
        
