# Subtitle Tag Injector

A desktop application for automatically inserting intro and outro subtitle tags into subtitle files without overlapping existing subtitles.

The application searches for available timing gaps, inserts subtitle events safely, and can automatically reduce tag duration when necessary.

Built with **Python**, **PySide6**, and **pysubs2**.

---

## Features

- Supports multiple subtitle formats
  - SRT
  - ASS
  - SSA
  - VTT

- Multiple insertion rules

- Insert at:
  - Beginning
  - End
  - Both

- Configurable search range

- Minimum safety gap between subtitles

- Automatic duration reduction

- Optional edge insertion

- Batch processing

- ZIP export

- Automatic subtitle encoding detection

- Drag & Drop support

- Portable Windows executable support

---

## How it works

### Start insertion

1. Try inserting before the first subtitle (optional).
2. Search the first **N** subtitle entries.
3. If no space exists:
   - reduce duration
   - search again
4. Repeat until minimum duration.
5. If still no position is available, skip the tag and report it in the log.

### End insertion

1. Search the last **N** subtitle entries.
2. If no space exists:
   - reduce duration
   - search again
3. Repeat until minimum duration.
4. If still no position exists and edge insertion is enabled:
   - append after the last subtitle.
5. Otherwise report failure.

---

## Requirements

Python 3.10+

---

## Installation

Clone the repository

```bash
git clone https://github.com/USERNAME/subtitle-tag-injector.git

cd subtitle-tag-injector
```

Create a virtual environment

```bash
python -m venv venv
```

Activate it

Windows

```bash
venv\Scripts\activate
```

Linux/macOS

```bash
source venv/bin/activate
```

Install dependencies

```bash
pip install -r requirements.txt
```

Run

```bash
python subtitle-tag-injector.py
```

---

## Building a Windows executable

Install PyInstaller

```bash
pip install pyinstaller
```

Build

```bash
pyinstaller ^
    --onefile ^
    --windowed ^
    --clean ^
    --name "Subtitle Tag Injector" ^
    subtitle-tag-injector.py
```

The executable will be created inside

```
dist/
```

---

## Dependencies

- PySide6
- pysubs2
- charset-normalizer
- PyInstaller (optional)

---

## Project structure

```
subtitle-tag-injector.py
requirements.txt
README.md
LICENSE
```

---

## Development

This project was developed collaboratively with ChatGPT (OpenAI).

The application idea, requirements, algorithms, feature design, testing, and overall direction were defined by the developer, while ChatGPT assisted with code generation, refactoring, documentation, debugging, and implementation details throughout the development process.

---

## License

MIT License
