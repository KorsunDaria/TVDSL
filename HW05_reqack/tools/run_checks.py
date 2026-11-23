#!/usr/bin/env python3
"""
Автозапуск всех проверок HW5 одной командой:   python3 tools/run_checks.py

"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
MODEL = ROOT / "model"
CLAIMS = ["s1", "s2", "s3", "l1", "l2"]
NUSMV = os.path.expanduser("~/labs/lib/NuSMV/NuSMV")
SPIN_MODES = {            
    "por": [],            
    "nored": ["-DNOREDUCE"],
    "bitstate": ["-DBITSTATE"],
}


def run(cmd, log, cwd=None):

    t0 = time.perf_counter()
    with open(log, "w", encoding="utf-8") as f:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT)
        _, _, ru = os.wait4(p.pid, 0)     
    sec = time.perf_counter() - t0
    div = 1024 * 1024 if sys.platform == "darwin" else 1024  
    return sec, ru.ru_maxrss / div


def text(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def cmd_out(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        return (r.stdout + r.stderr).strip().splitlines()[0]
    except Exception:
        return "?"


def main():
    LOGS.mkdir(exist_ok=True)
    rows_py, rows_smv, rows_spin, notes = [], [], [], []


    sec, mb = run([sys.executable, str(ROOT / "tools" / "verify.py"), "3"], LOGS / "python_verify.log")
    rows_py.append(f"| Python (явный перебор) | все | см. лог | {sec:.2f} | {mb:.0f} |")
    print("python: готово")

    versions = []
    if shutil.which(NUSMV):
        smv = str(MODEL / "reqack.smv")
        for name, args in (("bdd", ["-r"]),):
            log = LOGS / f"nusmv_{name}.log"
            sec, mb = run([NUSMV, *args, smv], log)
            t = text(log)
            if name == "bdd":
                versions.append(next((l.strip("* ") for l in t.splitlines() if "This is NuSMV" in l), "NuSMV ?"))
            verdicts = re.findall(r"-- specification\s+(.+?)\s+is (true|false)", t)
            nocex = re.findall(r"-- no counterexample found with bound \d+ for\s+(.+)", t)
            mode = "BDD" if name == "bdd" else "SAT/BMC k=10"
            v = "; ".join(f"{f.strip()} → {r}" for f, r in verdicts)
            if nocex:
                v += ("; " if v else "") + "; ".join(f"{f.strip()} → нет контрпримера до k" for f in nocex)
            rows_smv.append(f"| NuSMV, {mode} | {v or 'см. лог'} | {sec:.2f} | {mb:.0f} |")
            print(f"NuSMV {mode}: готово")
    else:
        notes.append("NuSMV не найден в PATH — пропущено.")
        print("NuSMV не найден — пропуск")

    if shutil.which("spin") and shutil.which("gcc"):
        versions.append(cmd_out(["spin", "-V"]))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shutil.copy(MODEL / "reqack.pml", tmp)
            subprocess.run(["spin", "-a", "reqack.pml"], cwd=tmp, check=True, capture_output=True)
            for mode, flags in SPIN_MODES.items():
                subprocess.run(["gcc", "-O2", *flags, "-o", f"pan_{mode}", "pan.c"],
                               cwd=tmp, check=True, capture_output=True)
                for c in CLAIMS:
                    log = LOGS / f"spin_{mode}_{c}.log"
                    sec, mb = run([f"./pan_{mode}", "-a", "-N", c], log, cwd=tmp)
                    t = text(log)
                    err = re.search(r"errors:\s*(\d+)", t)
                    st = re.search(r"(\d+)\s+states, stored", t)
                    verdict = "—" if not err else ("true" if err.group(1) == "0" else "false")
                    rows_spin.append(f"| Spin, {mode} | {c} | {verdict} | {st.group(1) if st else '?'} | {sec:.2f} | {mb:.0f} |")
                    if mode == "por" and c == "l2":     # трасса контрпримера, пока trail свежий
                        run([f"./pan_{mode}", "-r", "-N", "l2"], LOGS / "spin_l2_trail.log", cwd=tmp)
                        r = subprocess.run(["spin", "-t", "-p", "-ltl", "l2", "reqack.pml"],
                                           cwd=tmp, capture_output=True, text=True)
                        if r.returncode == 0 and r.stdout.strip():
                            with open(LOGS / "spin_l2_trail.log", "a", encoding="utf-8") as f:
                                f.write("\n--- spin -t -p -ltl l2 ---\n" + r.stdout)
                print(f"Spin {mode}: готово")
    else:
        notes.append("spin или gcc не найден в PATH — Spin пропущен.")
        print("spin/gcc не найден — пропуск")

    out = ["# Сводка запусков", "", f"Платформа: {sys.platform}; версии: " + "; ".join(versions or ["—"]), ""]
    out += notes + [""] if notes else []
    out += ["## Python", "", "| Инструмент | Свойства | Результат | Время, с | Пик RSS, МБ |", "|---|---|---|---|---|", *rows_py, ""]
    if rows_smv:
        out += ["## NuSMV", "", "| Режим | Вердикты | Время, с | Пик RSS, МБ |", "|---|---|---|---|", *rows_smv, ""]
    if rows_spin:
        out += ["## Spin (каждое свойство — отдельный запуск)", "",
                "| Режим | Свойство | Вердикт | states, stored | Время, с | Пик RSS, МБ |",
                "|---|---|---|---|---|---|", *rows_spin, ""]
    (LOGS / "summary.md").write_text("\n".join(out), encoding="utf-8")
    print(f"\nСводка: {LOGS / 'summary.md'}")


if __name__ == "__main__":
    main()
