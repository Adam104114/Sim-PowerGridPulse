import unittest
from core.physics_engine import GridParameters, GridPhysicsEngine
from core.generators import ThermalConfig, ThermalGenerator, BESSConfig, BatteryStorageSystem
from core.controllers import AGCConfig, AGCController, EconomicDispatcher

class TestGridPhysicsAndMechanics(unittest.TestCase):

    def test_steady_state_equilibrium(self):
        """Üretim ve tüketim eşitken RoCoF sıfır olmalı ve frekans 50.00 Hz'de sabit kalmalıdır."""
        params = GridParameters(nominal_freq=50.00, base_power_mva=1000.0, inertia_constant_h=4.0)
        engine = GridPhysicsEngine(params)

        freq = engine.step(total_generation_mw=300.0, total_demand_mw=300.0, dt=1.0)
        self.assertAlmostEqual(engine.rocof, 0.0, places=6)
        self.assertAlmostEqual(freq, 50.00, places=6)
        self.assertFalse(engine.is_collapsed)


    def test_swing_equation_analytical_accuracy(self):
        """Swing Equation sayısal çıktısı analitik formülle birebir örtüşmelidir."""
        params = GridParameters(nominal_freq=50.00, base_power_mva=1000.0, inertia_constant_h=2.5, damping_factor_d=1.5)
        engine = GridPhysicsEngine(params)

        # Delta_P = -50 MW, t=0 anında Delta_f = 0 olduğu için Damping = 0
        # Beklenen RoCoF = (50 / (2 * 2.5 * 1000)) * (-50) = -0.50 Hz/s
        freq = engine.step(total_generation_mw=250.0, total_demand_mw=300.0, dt=1.0)
        self.assertAlmostEqual(engine.rocof, -0.50, places=6)
        self.assertAlmostEqual(freq, 49.50, places=6)


    def test_blackout_protection_relay(self):
        """Frekans 48.50 Hz sınırının altına düştüğünde Blackout tetiklenmelidir."""
        params = GridParameters(nominal_freq=50.00, base_power_mva=1000.0, inertia_constant_h=2.0, blackout_low_hz=48.50)
        engine = GridPhysicsEngine(params)

        for _ in range(10):
            engine.step(total_generation_mw=100.0, total_demand_mw=300.0, dt=1.0)

        self.assertTrue(engine.is_collapsed)


    def test_thermal_ramp_rate_physical_limit(self):
        """Termik santral 1 saniyede max_ramp_rate_mw_s sınırından fazla güç artıramaz."""
        cfg = ThermalConfig(
            rated_power_mw=500.0,
            min_power_mw=50.0,
            turbine_time_const_s=2.0,
            max_ramp_rate_mw_s=3.0,
            marginal_cost_usd_mwh=40.0
        )
        gen = ThermalGenerator("Test_Thermal", cfg, initial_output_mw=100.0)
        gen.set_agc_dispatch(400.0)  # +300 MW ani talep

        new_output = gen.step(dt=1.0, grid_freq_hz=50.00, nominal_freq_hz=50.00)
        self.assertLessEqual(new_output - 100.0, 3.0 + 1e-6)


    def test_bess_soc_protection_and_efficiency(self):
        """Batarya min_soc (%10) sınırına indiğinde deşarjı kesmelidir."""
        cfg = BESSConfig(rated_power_mw=100.0, capacity_mwh=10.0, min_soc=0.10, efficiency=0.90, inverter_lag_s=0.1)
        bess = BatteryStorageSystem("Test_BESS", cfg, initial_soc=0.105)
        bess.set_power_command(100.0)  # Tam güç deşarj komutu

        # 36 saniye (0.01 saat) boyunca deşarj et -> Batarya boşalıp kilitlenmeli
        for _ in range(36):
            output = bess.step(dt=1.0, grid_freq_hz=49.50, nominal_freq_hz=50.00)

        self.assertLessEqual(bess.soc, 0.10)
        self.assertAlmostEqual(output, 0.0, places=2)


    def test_merit_order_economic_dispatch_and_n1_trip(self):
        """Ekonomik dağıtım en ucuz santrali doldurmalı ve N-1 arızada yükü sağlam santrale kaydırmalıdır."""
        cheap = ThermalGenerator("Cheap", ThermalConfig(200.0, 50.0, 5.0, 5.0, marginal_cost_usd_mwh=30.0), 50.0)
        mid = ThermalGenerator("Mid", ThermalConfig(200.0, 40.0, 5.0, 5.0, marginal_cost_usd_mwh=60.0), 40.0)
        expensive = ThermalGenerator("Expensive", ThermalConfig(150.0, 0.0, 2.0, 10.0, marginal_cost_usd_mwh=150.0), 0.0)
        fleet = [expensive, cheap, mid]  # Karışık sırada verildi

        # Toplam 260 MW talep: Cheap (200 MW) + Mid (60 MW) + Expensive (0 MW) olmalı
        EconomicDispatcher.allocate_power(260.0, fleet)
        self.assertAlmostEqual(cheap.agc_setpoint_mw, 200.0)
        self.assertAlmostEqual(mid.agc_setpoint_mw, 60.0)
        self.assertAlmostEqual(expensive.agc_setpoint_mw, 0.0)

        # N-1 Arıza: Cheap santrali arızalandı! Yük Mid (200 MW) + Expensive (60 MW) üzerine kaymalı
        cheap.trip()
        EconomicDispatcher.allocate_power(260.0, fleet)
        self.assertAlmostEqual(cheap.agc_setpoint_mw, 0.0)
        self.assertAlmostEqual(mid.agc_setpoint_mw, 200.0)
        self.assertAlmostEqual(expensive.agc_setpoint_mw, 60.0)

if __name__ == "__main__":
    unittest.main(verbosity=2)