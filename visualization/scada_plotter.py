from pathlib import Path
import sqlite3
import matplotlib.pyplot as plt

class ScadaDashboardPlotter:

    def __init__(self, db_path: str = "data/grid_telemetry.db"):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Veritabanı bulunamadı: {self.db_path}. Önce main.py çalıştırılmalıdır.")

    def _fetch_telemetry(self) -> dict[str, list]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT sim_time_s, demand_mw, solar_mw, thermal_mw, bess_mw, bess_soc, freq_hz
                FROM telemetry
                ORDER BY sim_time_s ASC
            """)
            rows = cursor.fetchall()
        return {
            "hours": [r[0] / 3600.0 for r in rows],
            "demand": [r[1] for r in rows],
            "solar": [r[2] for r in rows],
            "thermal": [r[3] for r in rows],
            "bess_mw": [r[4] for r in rows],
            "bess_soc": [r[5] for r in rows],
            "freq": [r[6] for r in rows],
        }


    def render_report(self, save_path: str = "data/scada_analysis.png") -> None:
        data = self._fetch_telemetry()
        hours = data["hours"]

        plt.style.use("seaborn-v0_8-darkgrid")
        fig, axes = plt.subplots(4, 1, figsize=(14, 13), sharex=False)
        fig.suptitle("Sim-PowerGridPulse — 24 Saatlik Şebeke Dinamiği & Frekans Analizi", fontsize=14, fontweight="bold")

        # --- PANEL 1: Güç Üretim ve Tüketim Dengesi (MW) ---
        ax1 = axes[0]
        ax1.plot(hours, data["demand"], label="Şehir Talebi (Demand)", color="#d62728", linewidth=1.8)
        ax1.plot(hours, data["thermal"], label="Termik Santral (MW)", color="#1f77b4", linewidth=1.5)
        ax1.fill_between(hours, 0, data["solar"], label="Güneş Üretimi (GES)", color="#ffbb00", alpha=0.45)
        ax1.set_ylabel("Güç (MW)")
        ax1.set_title("1. Aktif Güç Dengesi (Üretim Karması vs. Şehir Talebi)", loc="left", fontsize=11, fontweight="bold")
        ax1.set_xlim(0, 24)
        ax1.legend(loc="upper left", ncol=3)

        # --- PANEL 2: Şebeke Frekansı (Hz) ve Güvenlik Sınırları ---
        ax2 = axes[1]
        ax2.plot(hours, data["freq"], label="Şebeke Frekansı (Hz)", color="#2ca02c", linewidth=1.2)
        ax2.axhline(50.00, color="black", linestyle="--", linewidth=1.0, label="Nominal (50.00 Hz)")
        ax2.axhline(48.50, color="red", linestyle=":", linewidth=1.5, label="Blackout Alt Sınırı (48.50 Hz)")
        
        min_freq = min(data["freq"])
        min_idx = data["freq"].index(min_freq)
        ax2.annotate(
            f"Frequency Nadir: {min_freq:.3f} Hz ({hours[min_idx]:.2f}. saat)",
            xy=(hours[min_idx], min_freq),
            xytext=(hours[min_idx] + 1.5, min_freq + 0.15),
            arrowprops=dict(facecolor="red", shrink=0.08, width=1.5, headwidth=6),
            fontsize=9, fontweight="bold", color="darkred"
        )
        ax2.set_ylabel("Frekans (Hz)")
        ax2.set_title("2. Sistem Frekans Kararlılığı (Swing Equation Çıktısı)", loc="left", fontsize=11, fontweight="bold")
        ax2.set_xlim(0, 24)
        ax2.legend(loc="lower left", ncol=3)

        # --- PANEL 3: Batarya Depolama Sistemi (BESS) Güç ve SoC ---
        ax3 = axes[2]
        ax3_twin = ax3.twinx()
        l1 = ax3.plot(hours, data["bess_mw"], label="BESS Güç (+Deşarj / -Şarj MW)", color="#9467bd", alpha=0.75, linewidth=1.0)
        l2 = ax3_twin.plot(hours, data["bess_soc"], label="Batarya Doluluk (SoC %)", color="#ff7f0e", linewidth=2.0)
        ax3.axhline(0, color="gray", linewidth=0.8)
        ax3.set_ylabel("BESS Güç (MW)", color="#9467bd")
        ax3_twin.set_ylabel("SoC (%)", color="#ff7f0e")
        ax3_twin.set_ylim(0, 100)
        ax3.set_title("3. Batarya Enerji Depolama Sistemi (FFR Müdahalesi & SoC Durumu)", loc="left", fontsize=11, fontweight="bold")
        ax3.set_xlim(0, 24)
        lines = l1 + l2
        ax3.legend(lines, [l.get_label() for l in lines], loc="upper right")

        # --- PANEL 4: Saat 13:45 - 15:00 Arası Stres Testi Yakınlaştırma (Zoom) ---
        ax4 = axes[3]
        zoom_mask = [13.75 <= h <= 15.0 for h in hours]
        z_hours = [h for h, m in zip(hours, zoom_mask) if m]
        z_freq = [f for f, m in zip(data["freq"], zoom_mask) if m]
        z_bess = [b for b, m in zip(data["bess_mw"], zoom_mask) if m]

        ax4_twin = ax4.twinx()
        z1 = ax4.plot(z_hours, z_freq, label="Frekans (Hz)", color="#2ca02c", linewidth=1.8)
        z2 = ax4_twin.plot(z_hours, z_bess, label="BESS Müdahalesi (MW)", color="#9467bd", linestyle="--", linewidth=1.5)
        ax4.axhline(50.00, color="black", linestyle=":", linewidth=1.0)
        ax4.set_xlabel("Günün Saati (00:00 - 24:00)")
        ax4.set_ylabel("Frekans (Hz)", color="#2ca02c")
        ax4_twin.set_ylabel("BESS (MW)", color="#9467bd")
        ax4.set_title("4. STRES TESTİ ZOOM (14:00 Ağır Sanayi +65 MW Yük Şoku Tepkisi)", loc="left", fontsize=11, fontweight="bold")
        z_lines = z1 + z2
        ax4.legend(z_lines, [l.get_label() for l in z_lines], loc="lower right")

        plt.tight_layout(rect=[0, 0.02, 1, 0.96])
        plt.savefig(save_path, dpi=150)
        print(f"SCADA analiz grafiği kaydedildi: {save_path}")
        plt.show()

if __name__ == "__main__":
    plotter = ScadaDashboardPlotter()
    plotter.render_report()