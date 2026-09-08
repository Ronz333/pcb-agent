import gradio as gr
import os
import subprocess
import json
import shutil
import ezdxf
import ollama
import zipfile
import re
import uuid
from ezdxf.math import Area

# Konfiguration über Umgebungsvariablen
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama-api:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:32b")
BASE_WORK_DIR = "/app/workdir"

def extract_python_code(text):
    """Extrahiert sauberen Python-Code aus Markdown-Antworten des LLM."""
    match = re.search(r'```python\n(.*?)```', text, re.DOTALL)
    if match:
        return match.group(1)
    return text.replace('```python', '').replace('```', '')

def analyze_dxf_area(dxf_path):
    """Liest die Außenkontur aus der DXF-Datei aus und berechnet die Fläche."""
    try:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        outlines = msp.query('LWPOLYLINE[layer=="OUTLINE"]')

        if not outlines:
            return 0.0, "Hinweis: Kein Layer 'OUTLINE' gefunden. Nutze Standardbereich."

        points = outlines[0].get_points('xy')
        area = Area.polygon2d(points)
        return area, f"Platinenfläche erfolgreich ermittelt: {area:.1f} mm²"
    except Exception as e:
        return 0.0, f"DXF-Analyse übersprungen: {e}"

def execute_with_healing(client, system_prompt, user_prompt, script_path, work_dir, max_retries=3):
    """Führt KI-generierten Python-Code aus und fordert bei Fehlern Selbstkorrektur an."""
    current_prompt = user_prompt

    for attempt in range(max_retries):
        response = client.chat(model=OLLAMA_MODEL, messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': current_prompt}
        ])

        code = extract_python_code(response['message']['content'])

        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(code)

        result = subprocess.run(
            ['python3', script_path],
            cwd=work_dir,
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            return True, f"Erfolgreich nach {attempt+1} Durchlauf/Durchläufen."

        error_msg = result.stderr or result.stdout
        current_prompt = (
            f"Der Python-Code erzeugte folgenden Laufzeitfehler:\n"
            f"```text\n{error_msg}\n```\n"
            f"Bitte korrigiere den Code. Antworte AUSSCHLIESSLICH mit dem korrigierten Python-Code."
        )

    return False, f"Abbruch nach {max_retries} Versuchen. Fehler:\n{error_msg}"

def run_pcb_pipeline(prompt, dxf_file):
    """Vollständige Pipeline mit SPICE, ERC, DRC und Gerber-Export."""
    if dxf_file is None:
        yield None, "❌ Bitte laden Sie eine DXF-Datei der Platinenkontur hoch."
        return

    session_id = str(uuid.uuid4())[:8]
    work_dir = os.path.join(BASE_WORK_DIR, session_id)
    os.makedirs(work_dir, exist_ok=True)

    status_log = f"🚀 Starte Workflow (Session ID: {session_id})\n"
    status_log += f"Verbindung zu Ollama: {OLLAMA_HOST} (Modell: {OLLAMA_MODEL})\n\n"
    yield None, status_log

    client = ollama.Client(host=OLLAMA_HOST)

    try:
        # --- SCHRITT 1: DXF Upload & Geometrie-Prüfung ---
        dxf_path = os.path.join(work_dir, "board.dxf")
        shutil.copy(dxf_file.name, dxf_path)

        area, msg = analyze_dxf_area(dxf_path)
        status_log += f"[1/7] Geometrie: {msg}\n"
        yield None, status_log

        # --- SCHRITT 2: SKiDL Schaltplan-Code Generierung ---
        status_log += f"[2/7] Generiere SKiDL-Schaltplan und Netzliste...\n"
        yield None, status_log

        sys_skidl = (
            "Du bist ein Senior Hardware-Entwickler. Schreibe ein fehlerfreies SKiDL Python-Skript. "
            "Das Skript muss eine KiCad-Netzliste ('board.net') sowie eine SPICE-Netzliste ('circuit.cir') "
            "exportieren und ein KiCad-Schaltplandokument ('board.kicad_sch') speichern. Antworte NUR mit Python-Code."
        )

        success, msg = execute_with_healing(
            client, sys_skidl, f"Spezifikation: {prompt}",
            os.path.join(work_dir, "gen_schematic.py"), work_dir
        )
        status_log += f"      ↳ {msg}\n"
        yield None, status_log
        if not success: raise RuntimeError("SKiDL-Erzeugung fehlgeschlagen.")

        # --- SCHRITT 3: ngspice / PySpice Schaltungssimulation ---
        status_log += f"[3/7] Führe SPICE-Schaltungssimulation mit ngspice aus...\n"
        yield None, status_log

        spice_file = os.path.join(work_dir, "circuit.cir")
        if os.path.exists(spice_file):
            spice_res = subprocess.run(
                ["ngspice", "-b", spice_file],
                cwd=work_dir, capture_output=True, text=True
            )
            if spice_res.returncode == 0 and "Error" not in spice_res.stderr:
                status_log += f"      ↳ SPICE-Simulation erfolgreich und stabil.\n"
            else:
                status_log += f"      ⚠️ SPICE-Warnung: {spice_res.stderr[:200]}...\n"
        else:
            status_log += f"      ↳ Kein SPICE-Modell zur Simulation generiert (Übersprungen).\n"
        yield None, status_log

        # --- SCHRITT 4: KiCad Electrical Rules Check (ERC) ---
        status_log += f"[4/7] Führe KiCad Electrical Rules Check (ERC) aus...\n"
        yield None, status_log

        sch_file = os.path.join(work_dir, "board.kicad_sch")
        erc_report = os.path.join(work_dir, "erc_report.json")

        if os.path.exists(sch_file):
            subprocess.run([
                "kicad-cli", "sch", "erc",
                "--format", "json",
                "-o", erc_report, sch_file
            ], check=False)

            if os.path.exists(erc_report):
                with open(erc_report, 'r') as f:
                    erc_data = json.load(f)
                    violations = len(erc_data.get("violations", []))
                status_log += f"      ↳ ERC beendet: {violations} Fehler/Konflikte im Schaltplan.\n"
            else:
                status_log += f"      ↳ ERC ausgeführt.\n"
        else:
            status_log += f"      ↳ Kein `.kicad_sch` für CLI-ERC vorhanden (Übersprungen).\n"
        yield None, status_log

        # --- SCHRITT 5: pcbnew Layout & Platzierung ---
        status_log += f"[5/7] Platziere Bauteile auf der Platine (pcbnew)...\n"
        yield None, status_log

        sys_pcb = (
            "Du bist ein KiCad pcbnew-Experte. Schreibe ein Python-Skript, das 'board.dxf' auf Edge.Cuts importiert, "
            "die Netzliste 'board.net' einliest, Bauteile logisch platziert und als 'board.kicad_pcb' speichert. "
            "Antworte NUR mit Python-Code."
        )

        success, msg = execute_with_healing(
            client, sys_pcb, "Platziere die Bauteile auf dem Board.",
            os.path.join(work_dir, "gen_placement.py"), work_dir
        )
        status_log += f"      ↳ {msg}\n"
        yield None, status_log
        if not success: raise RuntimeError("Bauteilplatzierung fehlgeschlagen.")

        # --- SCHRITT 6: Autorouting (Freerouting CLI) ---
        status_log += f"[6/7] Starte Autorouting via Freerouting...\n"
        yield None, status_log

        board_pcb = os.path.join(work_dir, "board.kicad_pcb")
        board_dsn = os.path.join(work_dir, "board.dsn")
        board_ses = os.path.join(work_dir, "board.ses")

        subprocess.run(["kicad-cli", "pcb", "export", "dsn", "-o", board_dsn, board_pcb], check=True)
        subprocess.run(["java", "-jar", "/opt/freerouting.jar", "-de", board_dsn, "-s", board_ses, "-mp", "100"], check=True)
        subprocess.run(["kicad-cli", "pcb", "import", "ses", board_ses, board_pcb], check=True)

        status_log += f"      ↳ Routing abgeschlossen.\n"
        yield None, status_log

        # --- SCHRITT 7: DRC & Fertigungsexport ---
        status_log += f"[7/7] Führe DRC aus und exportiere Gerber-Dateien...\n"
        yield None, status_log

        drc_report = os.path.join(work_dir, "drc_report.json")
        subprocess.run(["kicad-cli", "pcb", "run-drc", "--format", "json", "-o", drc_report, board_pcb], check=False)

        gerber_dir = os.path.join(work_dir, "gerber")
        os.makedirs(gerber_dir, exist_ok=True)

        subprocess.run(["kicad-cli", "pcb", "export", "gerbers", "-o", gerber_dir, board_pcb], check=True)
        subprocess.run(["kicad-cli", "pcb", "export", "drill", "-o", gerber_dir, board_pcb], check=True)

        zip_path = os.path.join(BASE_WORK_DIR, f"PCB_Export_{session_id}.zip")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(gerber_dir):
                for file in files:
                    zipf.write(os.path.join(root, file), arcname=file)

        status_log += f"\n✅ Fertigstellung erfolgreich! ZIP-Archiv bereitgestellt."
        yield zip_path, status_log

    except Exception as e:
        status_log += f"\n❌ Fehler im Ablauf: {str(e)}"
        yield None, status_log

# Gradio Web Interface
with gr.Blocks(title="AI PCB Designer", theme=gr.themes.Monochrome()) as demo:
    gr.Markdown("# ⚡ AI PCB Designer Agent")
    gr.Markdown("Automatisierte Entwicklung von Schaltplänen und Layouts mit SPICE-Simulation, ERC, DRC und Gerber-Export.")

    with gr.Row():
        with gr.Column(scale=1):
            prompt_input = gr.Textbox(
                lines=6,
                label="Schaltungs-Spezifikation",
                placeholder="Wasserwaage für 12V/24V KFZ-Netz, ESP32-C3, BMI160 IMU, OLED-Header, EC11 Encoder..."
            )
            dxf_input = gr.File(label="Gehäuse-Kontur (.dxf)", file_types=[".dxf"])
            submit_btn = gr.Button("🚀 Platine Generieren", variant="primary")

        with gr.Column(scale=1):
            status_output = gr.Textbox(label="Prozess-Protokoll", interactive=False, lines=15)
            file_output = gr.File(label="Gerber & Drill ZIP-Archiv")

    submit_btn.click(
        fn=run_pcb_pipeline,
        inputs=[prompt_input, dxf_input],
        outputs=[file_output, status_output]
    )

if __name__ == "__main__":
    os.makedirs(BASE_WORK_DIR, exist_ok=True)
    demo.launch(server_name="0.0.0.0", server_port=7860)
