import glob
import os
import sys
import time
from deep_translator import GoogleTranslator, MyMemoryTranslator

# Wybierz silnik: "google" lub "mymemory"
ENGINE = "google"

SKIP_PHRASES = (
    "yts.mx",
    "yify",
    "subtitles by",
    "downloaded from",
    "official yify",
    "sync and corrected",
    "www.",
)


def build_translator():
    if ENGINE == "mymemory":
        return MyMemoryTranslator(
            source="en-GB",
            target="pl-PL",
            email="gemini.cli.user@gmail.com",
        )
    return GoogleTranslator(source="en", target="pl")


def parse_srt_blocks(content: str):
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    return normalized.split("\n\n")


def load_existing_progress(output_path: str, total_blocks: int):
    """Zwraca listę już przetłumaczonych bloków (lub None na wypadek pełnego przeliczenia)."""
    if not os.path.exists(output_path):
        return [None] * total_blocks

    with open(output_path, "r", encoding="utf-8", errors="ignore") as f:
        existing_content = f.read()

    existing_blocks = parse_srt_blocks(existing_content)
    result = [None] * total_blocks

    for idx, block in enumerate(existing_blocks):
        if idx >= total_blocks:
            break
        lines = block.split("\n")
        # Blok uznajemy za gotowy, jeśli ma indeks, czas i co najmniej jedną linię tekstu
        if len(lines) >= 3 and "-->" in lines[1]:
            result[idx] = block

    return result


def should_skip_line(line: str) -> bool:
    lower = line.lower()
    return any(phrase in lower for phrase in SKIP_PHRASES)


def translate_line_with_retry(translator, text: str, delay: float = 0.4, max_attempts: int = 8) -> str:
    if not text.strip() or should_skip_line(text):
        return text

    for attempt in range(1, max_attempts + 1):
        try:
            if delay > 0:
                time.sleep(delay)
            translated = translator.translate(text)
            return translated if translated else text
        except Exception as e:
            wait_time = min(300, 15 * attempt)
            print(
                f"\n[Błąd/Limit próba {attempt}/{max_attempts}] ({e}) -> pauza {wait_time}s dla: {text!r}",
                flush=True,
            )
            time.sleep(wait_time)

    return text


def translate_srt(input_path: str):
    if input_path.endswith(".srt"):
        output_path = input_path[:-4] + ".pl.srt"
    else:
        output_path = input_path + ".pl.srt"

    print(f"\n--- Tłumaczenie ({ENGINE}): {input_path} -> {output_path} ---", flush=True)

    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    raw_blocks = parse_srt_blocks(content)
    total = len(raw_blocks)
    print(f"Liczba bloków: {total}", flush=True)

    output_blocks = load_existing_progress(output_path, total)
    already_done = sum(1 for b in output_blocks if b is not None)
    if already_done > 0:
        print(f"Wznowiono z istniejącego pliku: gotowych {already_done}/{total} bloków.", flush=True)

    translator = build_translator()
    delay = 0.35 if ENGINE == "google" else 2.5
    newly_translated = 0

    for i, block in enumerate(raw_blocks):
        if output_blocks[i] is not None:
            continue

        lines = block.split("\n")
        if len(lines) < 2:
            output_blocks[i] = block
            continue

        index_line = lines[0].strip()
        time_line = lines[1].strip()
        text_lines = lines[2:]

        translated_lines = []
        for line in text_lines:
            if not line.strip():
                translated_lines.append("")
            else:
                # Zachowujemy kursywę <i>...</i> jeśli występuje
                stripped = line.strip()
                if stripped.startswith("<i>") and stripped.endswith("</i>") and len(stripped) > 7:
                    inner = stripped[3:-4]
                    tr_inner = translate_line_with_retry(translator, inner, delay=delay)
                    translated_lines.append(f"<i>{tr_inner}</i>")
                else:
                    translated_lines.append(translate_line_with_retry(translator, stripped, delay=delay))

        output_blocks[i] = "\n".join([index_line, time_line] + translated_lines)
        newly_translated += 1

        if newly_translated % 25 == 0 or (i + 1) == total:
            materialized = [b if b is not None else raw_blocks[idx] for idx, b in enumerate(output_blocks)]
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(materialized) + "\n")
            print(f"Postęp: {i + 1}/{total} bloków (zapisano do {output_path})", flush=True)

    materialized = [b if b is not None else raw_blocks[idx] for idx, b in enumerate(output_blocks)]
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(materialized) + "\n")

    print(f"Zakończono: {output_path}", flush=True)


def main():
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        all_srt = sorted(glob.glob("*.srt"))
        files = [f for f in all_srt if not f.endswith(".pl.srt")]

    if not files:
        print("Brak plików .srt do przetłumaczenia.")
        return

    for fpath in files:
        translate_srt(fpath)


if __name__ == "__main__":
    main()
