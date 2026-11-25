# core/physics_engine.py
from dataclasses import dataclass

@dataclass
class GridParameters:
    nominal_freq: float = 50.00      
    base_power_mva: float = 1000.0  
    inertia_constant_h: float = 4.5 
    damping_factor_d: float = 1.5  
    blackout_low_hz: float = 48.50   
    blackout_high_hz: float = 51.50  
class GridPhysicsEngine:

    def __init__(self, params: GridParameters):
        self.params = params
        self.current_freq: float = params.nominal_freq
        self.rocof: float = 0.0
        self.is_collapsed: bool = False

    def step(self, total_generation_mw: float, total_demand_mw: float, dt: float) -> float:
        
        if self.is_collapsed:
            return 0.0
     
        delta_f = self.current_freq - self.params.nominal_freq

        damping_power_mw = (self.params.damping_factor_d * delta_f) * (
            self.params.base_power_mva / self.params.nominal_freq
        )

        net_power_imbalance_mw = total_generation_mw - total_demand_mw - damping_power_mw

        self.rocof = (
            self.params.nominal_freq / (2.0 * self.params.inertia_constant_h * self.params.base_power_mva)
        ) * net_power_imbalance_mw

        self.current_freq += self.rocof * dt

        if not (self.params.blackout_low_hz <= self.current_freq <= self.params.blackout_high_hz):
            self.is_collapsed = True

        return self.current_freq