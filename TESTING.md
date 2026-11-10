# Testing Guide

Dokumen ini menjelaskan cara standar menjalankan test suite Elara2 di lokal.

## Prasyarat

- Python 3.12+
- Dependency terpasang:

```bash
pip install -r requirements-dev.txt
```

## 1) Quick Syntax Check

```bash
python -m compileall apps core database runtime skills utils
```

## 2) Jalankan Unit Test (unittest)

Jika `pytest` tidak tersedia di environment, gunakan:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

## 3) Jalankan Test Tertentu

Contoh:

```bash
python -m unittest discover -s tests -p "test_state_manager_concurrency.py"
python -m unittest discover -s tests -p "test_database_manager_deadlock_retry.py"
python -m unittest discover -s tests -p "test_trusted_artifact_json.py"
```

## 4) Quality Gate (PowerShell)

```powershell
.\scripts\quality_gate.ps1
```

## Catatan

- Beberapa test concurrency (khusus file/SQLite lock di Windows) sensitif terhadap cleanup file temporer.
- Untuk test integration/trading, pastikan env mode bukan live kecuali memang disengaja.

