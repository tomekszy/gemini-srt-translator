import glob
import json
import os
import re
import sys
import time
from google import genai
from google.genai import types
from google.genai.errors import APIError

PROJECT_URL = "https://github.com/tomekszy/gemini-srt-translator"

BATCH_SIZE = 25
REQUEST_DELAY = 2.0

DEFAULT_TARGET_LANG = "polski"

SKIP_PHRASES = [
    "yts.mx",
    "yify",
    "subtitles by",
    "downloaded from",
    "official yify",
    "sync and corrected",
    "www.",
]

EXCLUDED_MODEL_SUBSTRINGS = (
    "embedding",
    "imagen",
    "veo",
    "tts",
    "transcribe",
    "native-audio",
    "audio",
    "computer-use",
    "robotics",
    "deep-research",
    "aqa",
)

IDENTIFY_PROMPT_TEMPLATE = """Na podstawie nazwy pliku z napisami SRT rozpoznaj tytuł filmu, serialu lub anime, sprawdź czy taki tytuł istnieje i wygeneruj listę nazw własnych (imiona, nazwiska, nazwy miejsc, frakcji, maszyn, pojęć charakterystycznych), których nie należy tłumaczyć na język docelowy.

Wejście:
- Nazwa pliku: {filename}
- Wskazówka / poprawiony tytuł od użytkownika: {title_hint}

Zwróć WYŁĄCZNIE poprawny JSON w formacie:
{{
  "exists": true,
  "title": "Pełny, właściwy tytuł produkcji",
  "keywords": ["Nazwa1", "Nazwa2"]
}}

Zasady:
1. Jeśli nazwa pliku zawiera tagi wydania (np. 1080p, BluRay, x264, AAC, YTS.MX), odrzuć je przy ustalaniu tytułu.
2. Jeśli podano wskazówkę/poprawiony tytuł, oprzyj się na nim i zweryfikuj go.
3. "exists" ustaw na true, jeśli rozpoznany tytuł istnieje w znanych produkcjach (film/serial/anime), lub false, jeśli to niepewne/nieznane.
4. "keywords" powinno zawierać unikalne nazwy własne ze świata tego tytułu bez duplikatów."""

TRANSLATE_PROMPT_TEMPLATE = """Jesteś profesjonalnym tłumaczem napisów filmowych (SRT) tłumaczącym z wykrytego języka źródłowego na język: {target_lang} dla produkcji: {title}.

Zasady:
1. Automatycznie wykrywaj język źródłowy każdej kwestii i tłumacz naturalnym, żywym językiem docelowym ({target_lang}), dopasowanym do kontekstu i postaci.
2. Zachowuj nazwy własne ze świata tego tytułu: {proper_nouns}. Dopasowanie nazw własnych jest niewrażliwe na wielkość liter (case-insensitive) – jeśli w tekście źródłowym wystąpi nazwa z tej listy zapisana inną wielkością liter, zachowaj ją w podanej formie.
3. Otrzymasz listę obiektów {{"id": int, "text": string}}. Zwróć WYŁĄCZNIE poprawny JSON w formacie:
{{"translations": [{{"id": 0, "text": "..."}}, ...]}}
Zachowaj dokładnie te same wartości "id" i nie łącz ani nie pomijaj żadnego elementu."""


def model_sort_key(model_name: str) -> tuple:
    lower = model_name.lower()
    if "flash-lite" in lower:
        tier = 0
    elif "flash" in lower:
        tier = 1
    elif "pro" in lower:
        tier = 2
    else:
        tier = 3

    is_preview = 1 if ("preview" in lower or "exp" in lower) else 0
    ver_match = re.search(r"gemini-(\d+(?:\.\d+)?)", lower)
    version = float(ver_match.group(1)) if ver_match else 0.0

    return (tier, is_preview, -version, lower)


def discover_usable_models(client: genai.Client) -> list[str]:
    print("Pobieram listę dostępnych modeli z Gemini API...", flush=True)
    usable: list[str] = []

    for m in client.models.list():
        raw_name = getattr(m, "name", "") or ""
        short_name = raw_name.removeprefix("models/")
        lower = short_name.lower()

        if "gemini" not in lower:
            continue

        if any(bad in lower for bad in EXCLUDED_MODEL_SUBSTRINGS):
            continue

        actions = (
            getattr(m, "supported_actions", None)
            or getattr(m, "supported_generation_methods", None)
            or []
        )
        if actions and not any("generatecontent" in str(a).lower() for a in actions):
            continue

        usable.append(short_name)

    unique_sorted = sorted(set(usable), key=model_sort_key)
    print(
        f"Wykryto {len(unique_sorted)} obsługiwanych modeli tekstowych: {', '.join(unique_sorted)}",
        flush=True,
    )
    return unique_sorted


def strip_code_fences(raw_text: str) -> str:
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned


def call_gemini_raw(
    client: genai.Client,
    system_instruction: str,
    prompt_content: str,
    active_models: list[str],
    max_attempts: int = 40,
) -> str:
    model_idx = 0

    for attempt in range(1, max_attempts + 1):
        if not active_models:
            raise RuntimeError("Brak dostępnych modeli – wszystkie zostały wykluczone.")

        model = active_models[model_idx % len(active_models)]
        try:
            if REQUEST_DELAY > 0:
                time.sleep(REQUEST_DELAY)

            response = client.models.generate_content(
                model=model,
                contents=prompt_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )

            if not response.text:
                raise ValueError("Pusta odpowiedź z API")

            return response.text

        except APIError as e:
            if e.code == 404:
                print(
                    f"\n[{model} APIError 404] Model niedostępny – usuwam z listy i pomijam oczekiwanie.",
                    flush=True,
                )
                active_models.remove(model)
                continue

            wait_time = 4 if e.code in (429, 503) else min(30, 5 * attempt)
            print(
                f"\n[{model} APIError {e.code} próba {attempt}/{max_attempts}] -> przełączam/pauza {wait_time}s",
                flush=True,
            )
            model_idx += 1
            time.sleep(wait_time)
        except Exception as e:
            wait_time = 3
            print(
                f"\n[{model} Błąd zapytania próba {attempt}/{max_attempts}] ({e}) -> pauza {wait_time}s",
                flush=True,
            )
            model_idx += 1
            time.sleep(wait_time)

    raise RuntimeError("Nie udało się uzyskać odpowiedzi po wyczerpaniu prób.")


def infer_title_and_keywords(
    client: genai.Client,
    active_models: list[str],
    filename: str,
    override_title: str | None = None,
) -> tuple[bool, str, list[str]]:
    prompt = IDENTIFY_PROMPT_TEMPLATE.format(
        filename=os.path.basename(filename),
        title_hint=override_title if override_title else "(brak – odczytaj z nazwy pliku)",
    )
    raw = call_gemini_raw(
        client=client,
        system_instruction="Jesteś asystentem rozpoznającym tytuły filmów i seriali oraz ich nazwy własne. Zwracaj wyłącznie poprawny JSON.",
        prompt_content=prompt,
        active_models=active_models,
        max_attempts=15,
    )
    data = json.loads(strip_code_fences(raw))
    exists = bool(data.get("exists", False))
    title = str(data.get("title", "")).strip() or os.path.basename(filename)
    raw_keywords = data.get("keywords", [])
    keywords: list[str] = []
    seen = set()
    if isinstance(raw_keywords, list):
        for kw in raw_keywords:
            item = str(kw).strip()
            if item and item.lower() not in seen:
                seen.add(item.lower())
                keywords.append(item)
    return exists, title, keywords


def edit_keywords_interactive(initial_keywords: list[str]) -> list[str]:
    keywords = list(initial_keywords)
    while True:
        print(
            f"\nLista słów kluczowych ({len(keywords)}): "
            f"{', '.join(keywords) if keywords else '(brak)'}",
            flush=True,
        )
        action = input(
            "Zatwierdź Enterem, wpisz '+nazwa' (lub 'nazwa') aby dodać, albo '-nazwa' aby usunąć: "
        ).strip()

        if not action:
            return keywords

        if action.startswith("-"):
            target = action[1:].strip().lower()
            before = len(keywords)
            keywords = [k for k in keywords if k.lower() != target]
            if len(keywords) == before:
                print(f"Nie znaleziono słowa kluczowego: '{action[1:].strip()}'", flush=True)
        elif action.startswith("+"):
            added = action[1:].strip()
            if added and added.lower() not in {k.lower() for k in keywords}:
                keywords.append(added)
        else:
            if action.lower() not in {k.lower() for k in keywords}:
                keywords.append(action)


def prompt_translation_config(
    client: genai.Client,
    active_models: list[str],
    sample_file: str,
) -> tuple[str, list[str], str, int]:
    print("\n--- Konfiguracja tłumaczenia ---", flush=True)

    override_title: str | None = None
    while True:
        print(f"Analizuję nazwę pliku: {os.path.basename(sample_file)}...", flush=True)
        exists, title, keywords = infer_title_and_keywords(
            client=client,
            active_models=active_models,
            filename=sample_file,
            override_title=override_title,
        )
        print(f"\nRozpoznany tytuł: {title}", flush=True)
        print(f"Czy taki tytuł istnieje w bazie wiedzy modelu?: {'TAK' if exists else 'NIE / NIEPEWNE'}", flush=True)
        print(f"Wygenerowane słowa kluczowe: {', '.join(keywords) if keywords else '(brak)'}", flush=True)
        print(f"Pomijane frazy napisowe: {', '.join(SKIP_PHRASES)}", flush=True)

        decision = input(
            "Potwierdź tytuł Enterem lub wpisz poprawny tytuł, aby odpytać Gemini ponownie: "
        ).strip()

        if not decision:
            break
        override_title = decision

    keywords = edit_keywords_interactive(keywords)

    target_lang_raw = input(f"\nNa jaki język tłumaczymy napisy? [{DEFAULT_TARGET_LANG}]: ").strip()
    target_lang = target_lang_raw if target_lang_raw else DEFAULT_TARGET_LANG

    start_raw = input(
        "Od którego numeru napisu (bloku SRT) tłumaczymy plik? [Enter = od początku (0)]: "
    ).strip()

    if not start_raw:
        start_number = 0
    else:
        try:
            start_number = max(0, int(start_raw))
        except ValueError:
            print("Nieprawidłowy numer – rozpoczynam od początku (0).", flush=True)
            start_number = 0

    return title, keywords, target_lang, start_number


def build_system_instruction(title: str, keywords: list[str], target_lang: str) -> str:
    clean_title = title.strip() if title.strip() else "nieokreślony tytuł"
    clean_target = target_lang.strip() if target_lang.strip() else DEFAULT_TARGET_LANG
    clean_keywords = [k.strip() for k in keywords if k.strip()]
    formatted_nouns = ", ".join(clean_keywords) if clean_keywords else "brak"

    return TRANSLATE_PROMPT_TEMPLATE.format(
        title=clean_title,
        proper_nouns=formatted_nouns,
        target_lang=clean_target,
    )


def parse_srt_blocks(content: str) -> list[str]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized.split("\n\n") if normalized else []


def extract_block_number(block: str, fallback_index: int) -> int:
    first_line = block.split("\n", 1)[0].strip().lstrip("\ufeff")
    if first_line.isdigit():
        return int(first_line)
    return fallback_index + 1


def is_banner_block(block: str) -> bool:
    return "Przetłumaczono za pomocą:" in block and PROJECT_URL in block


def parse_timestamp_ms(ts: str) -> int:
    match = re.match(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", ts.strip())
    if not match:
        return 0
    h, m, s, ms = map(int, match.groups())
    return ((h * 3600 + m * 60 + s) * 1000) + ms


def format_timestamp_ms(total_ms: int) -> str:
    total_ms = max(0, total_ms)
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    s = total_sec % 60
    total_min = total_sec // 60
    m = total_min % 60
    h = total_min // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def extract_last_end_ms(blocks: list[str]) -> int:
    last_end = 0
    for block in blocks:
        if is_banner_block(block):
            continue
        lines = block.split("\n")
        if len(lines) >= 2 and "-->" in lines[1]:
            parts = lines[1].split("-->")
            if len(parts) == 2:
                end_ms = parse_timestamp_ms(parts[1])
                if end_ms > last_end:
                    last_end = end_ms
    return last_end


def build_header_banner() -> str:
    return (
        "0\n"
        "00:00:00,000 --> 00:00:02,000\n"
        "Przetłumaczono za pomocą: gemini-srt-translator\n"
        f"{PROJECT_URL}"
    )


def build_footer_banner(blocks: list[str]) -> str:
    last_end_ms = extract_last_end_ms(blocks)
    start_ms = last_end_ms + 1000
    end_ms = start_ms + 4000
    next_index = len(blocks) + 1
    return (
        f"{next_index}\n"
        f"{format_timestamp_ms(start_ms)} --> {format_timestamp_ms(end_ms)}\n"
        "Przetłumaczono za pomocą: gemini-srt-translator\n"
        f"{PROJECT_URL}"
    )


def wrap_with_banners(body_blocks: list[str]) -> list[str]:
    clean_body = [b for b in body_blocks if not is_banner_block(b)]
    return [build_header_banner()] + clean_body + [build_footer_banner(clean_body)]


def should_skip_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    lower = stripped.lower()
    return any(phrase in lower for phrase in SKIP_PHRASES)


def load_existing_blocks_by_number(output_path: str) -> dict[int, str]:
    if not os.path.exists(output_path):
        return {}

    with open(output_path, "r", encoding="utf-8", errors="ignore") as f:
        existing_blocks = [b for b in parse_srt_blocks(f.read()) if not is_banner_block(b)]

    by_num: dict[int, str] = {}
    for idx, block in enumerate(existing_blocks):
        lines = block.split("\n")
        if len(lines) >= 3 and "-->" in lines[1]:
            num = extract_block_number(block, idx)
            by_num[num] = block
    return by_num


def parse_id_map(raw_text: str) -> dict[int, str]:
    data = json.loads(strip_code_fences(raw_text))
    items = data.get("translations") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Brak listy 'translations' w odpowiedzi")

    mapping = {}
    for entry in items:
        if isinstance(entry, dict) and "id" in entry and "text" in entry:
            mapping[int(entry["id"])] = str(entry["text"])
    return mapping


def call_gemini_batch(
    client: genai.Client,
    system_instruction: str,
    active_models: list[str],
    lines: list[str],
    max_attempts: int = 40,
) -> list[str]:
    payload_items = [{"id": idx, "text": txt} for idx, txt in enumerate(lines)]
    payload = json.dumps({"items": payload_items}, ensure_ascii=False)
    model_idx = 0

    for attempt in range(1, max_attempts + 1):
        if not active_models:
            print("\n[Brak dostępnych modeli] Wszystkie modele z listy zostały wykluczone.", flush=True)
            break

        model = active_models[model_idx % len(active_models)]
        try:
            if REQUEST_DELAY > 0:
                time.sleep(REQUEST_DELAY)

            response = client.models.generate_content(
                model=model,
                contents=payload,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )

            if not response.text:
                raise ValueError("Pusta odpowiedź z API")

            id_map = parse_id_map(response.text)
            missing = [idx for idx in range(len(lines)) if idx not in id_map]
            if missing:
                raise ValueError(f"Brakujące ID w odpowiedzi: {missing}")

            return [id_map[idx] for idx in range(len(lines))]

        except APIError as e:
            if e.code == 404:
                print(
                    f"\n[{model} APIError 404] Model niedostępny – usuwam z listy i pomijam oczekiwanie.",
                    flush=True,
                )
                active_models.remove(model)
                continue

            wait_time = 4 if e.code in (429, 503) else min(30, 5 * attempt)
            print(
                f"\n[{model} APIError {e.code} próba {attempt}/{max_attempts}] -> przełączam/pauza {wait_time}s",
                flush=True,
            )
            model_idx += 1
            time.sleep(wait_time)
        except Exception as e:
            wait_time = 3
            print(
                f"\n[{model} Błąd paczki próba {attempt}/{max_attempts}] ({e}) -> pauza {wait_time}s",
                flush=True,
            )
            model_idx += 1
            time.sleep(wait_time)

    if len(lines) > 1 and active_models:
        print("\n[Fallback] Rozbijam paczkę na połówki...", flush=True)
        mid = len(lines) // 2
        return (
            call_gemini_batch(client, system_instruction, active_models, lines[:mid], 8)
            + call_gemini_batch(client, system_instruction, active_models, lines[mid:], 8)
        )

    return lines


def build_output_path(input_path: str, target_lang: str) -> str:
    lower_lang = target_lang.strip().lower()
    if lower_lang in ("polski", "polish", "pl"):
        suffix = ".pl.srt"
    elif re.fullmatch(r"[a-z]{2,3}", lower_lang):
        suffix = f".{lower_lang}.srt"
    else:
        suffix = ".translated.srt"

    return input_path[:-4] + suffix if input_path.endswith(".srt") else input_path + suffix


def write_srt_file(output_path: str, body_blocks: list[str]) -> None:
    final_blocks = wrap_with_banners(body_blocks)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(final_blocks) + "\n")


def translate_srt(
    client: genai.Client,
    system_instruction: str,
    active_models: list[str],
    target_lang: str,
    input_path: str,
    start_number: int = 0,
):
    output_path = build_output_path(input_path, target_lang)
    print(f"\n--- Tłumaczenie: {input_path} -> {output_path} ---", flush=True)

    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_blocks = [b for b in parse_srt_blocks(f.read()) if not is_banner_block(b)]

    total = len(raw_blocks)
    output_blocks: list[str | None] = [None] * total
    existing_by_num = load_existing_blocks_by_number(output_path)

    if start_number > 1:
        kept_count = 0
        for i, block in enumerate(raw_blocks):
            block_num = extract_block_number(block, i)
            if block_num < start_number:
                if block_num in existing_by_num:
                    output_blocks[i] = existing_by_num[block_num]
                    kept_count += 1
                else:
                    output_blocks[i] = block
        print(
            f"Zachowano {kept_count} przetłumaczonych bloków sprzed numeru {start_number}. "
            f"Tłumaczenie startuje od bloku #{start_number}.",
            flush=True,
        )
    else:
        print("Rozpoczynam tłumaczenie od początku (blok #1).", flush=True)

    pending_items = []
    parsed_blocks = []

    for i, block in enumerate(raw_blocks):
        lines = block.split("\n")
        if len(lines) < 3 or "-->" not in lines[1]:
            parsed_blocks.append((None, None, []))
            if output_blocks[i] is None:
                output_blocks[i] = block
            continue

        index_line = lines[0].strip()
        time_line = lines[1].strip()
        text_lines = lines[2:]
        parsed_blocks.append((index_line, time_line, text_lines))

        if output_blocks[i] is None:
            for t_idx, line in enumerate(text_lines):
                stripped = line.strip()
                if should_skip_line(stripped):
                    continue
                has_italics = stripped.startswith("<i>") and stripped.endswith("</i>") and len(stripped) > 7
                inner = stripped[3:-4] if has_italics else stripped
                pending_items.append((i, t_idx, has_italics, inner))

    materialized = [b if b is not None else raw_blocks[idx] for idx, b in enumerate(output_blocks)]
    write_srt_file(output_path, materialized)

    if not pending_items:
        print("Brak linii do przetłumaczenia.", flush=True)
        return

    translated_map = {}

    for start in range(0, len(pending_items), BATCH_SIZE):
        chunk = pending_items[start : start + BATCH_SIZE]
        translated_texts = call_gemini_batch(
            client,
            system_instruction,
            active_models,
            [item[3] for item in chunk],
        )

        for item, tr_text in zip(chunk, translated_texts):
            b_idx, t_idx, has_italics, _ = item
            translated_map[(b_idx, t_idx)] = f"<i>{tr_text}</i>" if has_italics else tr_text

        for b_idx in {item[0] for item in chunk}:
            index_line, time_line, text_lines = parsed_blocks[b_idx]
            new_text_lines = [
                translated_map.get((b_idx, t_idx), orig_line)
                for t_idx, orig_line in enumerate(text_lines)
            ]
            output_blocks[b_idx] = "\n".join([index_line, time_line] + new_text_lines)

        materialized = [b if b is not None else raw_blocks[idx] for idx, b in enumerate(output_blocks)]
        write_srt_file(output_path, materialized)

        print(f"Postęp linii: {min(start + BATCH_SIZE, len(pending_items))}/{len(pending_items)}", flush=True)

    print(f"Zakończono: {output_path}", flush=True)


def main():
    if not os.environ.get("GEMINI_API_KEY"):
        print("Błąd: brak zmiennej środowiskowej GEMINI_API_KEY.", file=sys.stderr)
        sys.exit(1)

    files = sys.argv[1:] if len(sys.argv) > 1 else [f for f in sorted(glob.glob("*.srt")) if not f.endswith(".pl.srt")]

    if not files:
        print("Brak plików .srt do przetłumaczenia.")
        return

    client = genai.Client()
    active_models = discover_usable_models(client)
    if not active_models:
        print("Błąd: nie znaleziono żadnych obsługiwanych modeli Gemini na tym kluczu API.", file=sys.stderr)
        sys.exit(1)

    title, keywords, target_lang, start_number = prompt_translation_config(
        client=client,
        active_models=active_models,
        sample_file=files[0],
    )
    system_instruction = build_system_instruction(title, keywords, target_lang)

    for idx, fpath in enumerate(files):
        file_start = start_number if idx == 0 else 0
        translate_srt(
            client=client,
            system_instruction=system_instruction,
            active_models=active_models,
            target_lang=target_lang,
            input_path=fpath,
            start_number=file_start,
        )


if __name__ == "__main__":
    main()
