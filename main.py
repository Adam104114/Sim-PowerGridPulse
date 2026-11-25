# main.py
import time
from core.physics_engine import GridParameters, GridPhysicsEngine
from core.generators import ThermalConfig, ThermalGenerator, BESSConfig, BatteryStorageSystem, SolarFarm
from core.controllers import AGCConfig, AGCController, EconomicDispatcher, CityLoadProfile
from infrastructure.telemetry_repo import TelemetryRecord, SQLiteTelemetryRepository

def format_sim_time(sim_time_s: float) -> str:
    hours = int(sim_time_s // 3600) % 24
    minutes = int((sim_time_s % 3600) // 60)
    seconds = int(sim_time_s % 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def run_simulation() -> None:
    start_wall_time = time.perf_counter()

    # 1. Düşük Ataletli Modern Şebeke Parametreleri (H = 2.8s)
    grid_params = GridParameters(
        nominal_freq=50.00,
        base_power_mva=1000.0,
        inertia_constant_h=2.8,   # Yenilenebilir payı arttığı için atalet düştü!
        damping_factor_d=1.5
    )

    # 2. Merit Order Santral Portföyü (Ucuz/Yavaş -> Pahalı/Çevik)
    coal = ThermalGenerator(
        "Coal_Base (35$/MWh)",
        ThermalConfig(rated_power_mw=260.0, min_power_mw=80.0, turbine_time_const_s=15.0, max_ramp_rate_mw_s=1.2, marginal_cost_usd_mwh=35.0),
        initial_output_mw=180.0
    )
    ccgt = ThermalGenerator(
        "CCGT_Mid (65$/MWh)",
        ThermalConfig(rated_power_mw=200.0, min_power_mw=40.0, turbine_time_const_s=6.0, max_ramp_rate_mw_s=3.5, marginal_cost_usd_mwh=65.0),
        initial_output_mw=60.0
    )
    peaker = ThermalGenerator(
        "OCGT_Peaker (140$/MWh)",
        ThermalConfig(rated_power_mw=150.0, min_power_mw=0.0, turbine_time_const_s=1.5, max_ramp_rate_mw_s=12.0, marginal_cost_usd_mwh=140.0),
        initial_output_mw=20.0
    )
    thermal_fleet = [coal, ccgt, peaker]

    bess = BatteryStorageSystem("BESS-1", BESSConfig(rated_power_mw=120.0, capacity_mwh=240.0), initial_soc=0.65)
    solar = SolarFarm("GES-1", rated_power_mw=220.0, seed=42)

    physics = GridPhysicsEngine(grid_params)
    agc = AGCController(AGCConfig(), base_thermal_dispatch_mw=260.0)
    city_load = CityLoadProfile(base_load_mw=260.0, seed=42)
    repo = SQLiteTelemetryRepository("data/grid_telemetry.db")

    dt = 1.0
    total_steps = 86400
    telemetry_buffer: list[TelemetryRecord] = []

    freq_nadir = 50.00
    freq_zenith = 50.00
    max_abs_rocof = 0.0
    bess_discharge_mwh = 0.0
    bess_charge_mwh = 0.0
    n1_triggered = False

    print("Sim-PowerGridPulse (Faz 3 & 4: Economic Dispatch + N-1 Kriz Testi) başlatılıyor...")

    for step_idx in range(total_steps):
        sim_time_s = step_idx * dt

        # N-1 CONTINGENCY OLAYI: Saat 19:30'da (70200. sn) CCGT santrali arızalanıp devreden çıkar!
        if not n1_triggered and sim_time_s >= 70200.0:
            lost_mw = ccgt.current_output_mw
            ccgt.trip()
            n1_triggered = True
            print(f"[OLAY - {format_sim_time(sim_time_s)}] N-1 ARIZA: {ccgt.name} devreden çıktı! Anlık Kayıp: -{lost_mw:.1f} MW")

        # Sağlam santrallerin anlık toplam min/max kapasiteleri
        active_min_mw = sum(g.config.min_power_mw for g in thermal_fleet if not g.is_tripped)
        active_max_mw = sum(g.config.rated_power_mw for g in thermal_fleet if not g.is_tripped)

        # A. AGC & Economic Dispatch
        total_thermal_cmd, bess_cmd = agc.compute_dispatch(
            dt=dt,
            current_freq_hz=physics.current_freq,
            nominal_freq_hz=grid_params.nominal_freq,
            rocof_hz_s=physics.rocof,
            bess_soc=bess.soc,
            total_thermal_min_mw=active_min_mw,
            total_thermal_max_mw=active_max_mw
        )
        EconomicDispatcher.allocate_power(total_thermal_cmd, thermal_fleet)
        bess.set_power_command(bess_cmd)

        # B. Fiziksel Adımlar
        p_solar = solar.step(dt, physics.current_freq, grid_params.nominal_freq, sim_time_s=sim_time_s)
        p_thermal = sum(g.step(dt, physics.current_freq, grid_params.nominal_freq) for g in thermal_fleet)
        p_bess = bess.step(dt, physics.current_freq, grid_params.nominal_freq)
        p_demand = city_load.get_demand_mw(sim_time_s, dt)

        # C. Şebeke Fiziği
        freq_hz = physics.step(total_generation_mw=(p_solar + p_thermal + p_bess), total_demand_mw=p_demand, dt=dt)

        # D. KPI & Telemetri
        freq_nadir = min(freq_nadir, freq_hz)
        freq_zenith = max(freq_zenith, freq_hz)
        max_abs_rocof = max(max_abs_rocof, abs(physics.rocof))
        if p_bess > 0:
            bess_discharge_mwh += p_bess * (dt / 3600.0)
        else:
            bess_charge_mwh += abs(p_bess) * (dt / 3600.0)

        if step_idx % 5 == 0:
            telemetry_buffer.append(
                TelemetryRecord(
                    sim_time_s=sim_time_s,
                    hour_str=format_sim_time(sim_time_s),
                    demand_mw=round(p_demand, 2),
                    solar_mw=round(p_solar, 2),
                    thermal_mw=round(p_thermal, 2),
                    bess_mw=round(p_bess, 2),
                    bess_soc=round(bess.soc * 100.0, 2),
                    freq_hz=round(freq_hz, 4),
                    rocof_hz_s=round(physics.rocof, 4)
                )
            )

        if physics.is_collapsed:
            print(f"\n[BLACKOUT] Şebeke {format_sim_time(sim_time_s)} anında çöktü! Frekans: {freq_hz:.3f} Hz")
            break

    repo.save_batch(telemetry_buffer)
    elapsed_ms = (time.perf_counter() - start_wall_time) * 1000.0
    total_cost = sum(g.total_cost_usd for g in thermal_fleet)

    print("\n" + "=" * 68)
    print("SIM-POWERGRIDPULSE — ECONOMIC DISPATCH & N-1 KRİZ ANALİZ RAPORU")
    print("=" * 68)
    print(f"Şebeke Durumu          : {'KARARLI (N-1 KRİZİ ATLATILDI)' if not physics.is_collapsed else 'BLACKOUT (ÇÖKTÜ)'}")
    print(f"Frequency Nadir (Min)  : {freq_nadir:.4f} Hz  (Blackout Sınırı: 48.5000 Hz)")
    print(f"Max |RoCoF|            : {max_abs_rocof:.4f} Hz/s")
    print(f"BESS Deşarj / Şarj     : {bess_discharge_mwh:.2f} MWh / {bess_charge_mwh:.2f} MWh (Son SoC: %{bess.soc*100:.1f})")
    print("-" * 68)
    print("SANTRAL BAZLI EKONOMİK DAĞITIM MALİYETLERİ (24 SAAT):")
    for g in thermal_fleet:
        status_tag = "[ARIZALI - TRIP]" if g.is_tripped else "[AKTİF]"
        print(f"  * {g.name:<24} : ${g.total_cost_usd:>10,.2f}  {status_tag}")
    print(f"TOPLAM ÜRETİM MALİYETİ : ${total_cost:>10,.2f}  (Süre: {elapsed_ms:.0f} ms)")
    print("=" * 68)

if __name__ == "__main__":
    run_simulation()