import * as fs from "fs";
import * as path from "path";
import * as readline from "readline";
import { GoogleGenAI } from "@google/genai";
import * as dotenv from "dotenv";

dotenv.config();

const PROJECT_URL = "https://github.com/tomekszy/gemini-srt-translator";

const BATCH_SIZE = 25;
const REQUEST_DELAY_MS = 2000;
const DEFAULT_TARGET_LANG = "polski";

const LANG_CODE_NAMES: Record<string, string> = {
    en: "angielski",
    pl: "polski",
    de: "niemiecki",
    ja: "japoński",
    jp: "japoński",
    es: "hiszpański",
    fr: "francuski",
    it: "włoski",
    ru: "rosyjski",
    uk: "ukraiński",
    pt: "portugalski",
    ko: "koreański",
    zh: "chiński",
};

const SKIP_PHRASES: readonly string[] = [
    "yts.mx",
    "yify",
    "subtitles by",
    "downloaded from",
    "official yify",
    "sync and corrected",
    "www.",
];

const EXCLUDED_MODEL_SUBSTRINGS: readonly string[] = [
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
];

const IDENTIFY_PROMPT_TEMPLATE = `Na podstawie nazwy pliku z napisami SRT rozpoznaj tytuł filmu, serialu lub anime, sprawdź czy taki tytuł istnieje i wygeneruj listę nazw własnych (imiona, nazwiska, nazwy miejsc, frakcji, maszyn, pojęć charakterystycznych), których nie należy tłumaczyć na język docelowy.

Wejście:
- Nazwa pliku: {filename}
- Wskazówka / poprawiony tytuł od użytkownika: {title_hint}

Zwróć WYŁĄCZNIE poprawny JSON w formacie:
{
  "exists": true,
  "title": "Pełny, właściwy tytuł produkcji",
  "keywords": ["Nazwa1", "Nazwa2"]
}

Zasady:
1. Jeśli nazwa pliku zawiera tagi wydania (np. 1080p, BluRay, x264, AAC, YTS.MX) lub kody języka (np. .en.srt), odrzuć je przy ustalaniu tytułu.
2. Jeśli podano wskazówkę/poprawiony tytuł, oprzyj się na nim i zweryfikuj go.
3. "exists" ustaw na true, jeśli rozpoznany tytuł istnieje w znanych produkcjach (film/serial/anime), lub false, jeśli to niepewne/nieznane.
4. "keywords" powinno zawierać unikalne nazwy własne ze świata tego tytułu bez duplikatów.`;

const TRANSLATE_PROMPT_TEMPLATE = `Jesteś profesjonalnym tłumaczem napisów filmowych (SRT) tłumaczącym z języka źródłowego ({source_lang}) na język docelowy ({target_lang}) dla produkcji: {title}.

Zasady:
1. Tłumacz naturalnym, żywym językiem docelowym ({target_lang}), dopasowanym do kontekstu i postaci (w razie niejasności wspieraj się automatycznym wykryciem języka z treści napisów).
2. Zachowuj nazwy własne ze świata tego tytułu: {proper_nouns}. Dopasowanie nazw własnych jest niewrażliwe na wielkość liter (case-insensitive) – jeśli w tekście źródłowym wystąpi nazwa z tej listy zapisana inną wielkością liter, zachowaj ją w podanej formie.
3. Otrzymasz listę obiektów {"id": int, "text": string}. Zwróć WYŁĄCZNIE poprawny JSON w formacie:
{"translations": [{"id": 0, "text": "..."}, ...]}
Zachowaj dokładnie te same wartości "id" i nie łącz ani nie pomijaj żadnego elementu.`;

interface CliOptions {
    files: string[];
    startNumber?: number;
    targetLang?: string;
    titleHint?: string;
    autoYes: boolean;
}

interface TranslationItem {
    id: number;
    text: string;
}

interface IdentifyResponse {
    exists?: boolean;
    title?: string;
    keywords?: unknown[];
}

interface PendingLine {
    blockIndex: number;
    textLineIndex: number;
    hasItalics: boolean;
    innerText: string;
}

interface ParsedSrtBlock {
    indexLine: string | null;
    timeLine: string | null;
    textLines: string[];
}

function parseCliArgs(argv: string[]): CliOptions {
    const files: string[] = [];
    let startNumber: number | undefined;
    let targetLang: string | undefined;
    let titleHint: string | undefined;
    let autoYes = false;

    for (let i = 0; i < argv.length; i++) {
        const arg = argv[i];

        if (arg === "--yes" || arg === "-y") {
            autoYes = true;
            continue;
        }

        if (arg === "--start" || arg === "-s") {
            const val = argv[++i];
            if (!val || Number.isNaN(Number.parseInt(val, 10)) || Number.parseInt(val, 10) < 0) {
                throw new Error(`Nieprawidłowa wartość dla ${arg}: '${val ?? ""}'`);
            }
            startNumber = Number.parseInt(val, 10);
            continue;
        }

        if (arg.startsWith("--start=")) {
            const val = arg.slice("--start=".length);
            if (!val || Number.isNaN(Number.parseInt(val, 10)) || Number.parseInt(val, 10) < 0) {
                throw new Error(`Nieprawidłowa wartość dla --start: '${val}'`);
            }
            startNumber = Number.parseInt(val, 10);
            continue;
        }

        if (arg === "--lang" || arg === "-l") {
            const val = argv[++i];
            if (!val || !val.trim()) {
                throw new Error(`Brak wartości dla ${arg}`);
            }
            targetLang = val.trim();
            continue;
        }

        if (arg.startsWith("--lang=")) {
            const val = arg.slice("--lang=".length).trim();
            if (!val) {
                throw new Error("Brak wartości dla --lang");
            }
            targetLang = val;
            continue;
        }

        if (arg === "--title" || arg === "-t") {
            const val = argv[++i];
            if (!val || !val.trim()) {
                throw new Error(`Brak wartości dla ${arg}`);
            }
            titleHint = val.trim();
            continue;
        }

        if (arg.startsWith("--title=")) {
            const val = arg.slice("--title=".length).trim();
            if (!val) {
                throw new Error("Brak wartości dla --title");
            }
            titleHint = val;
            continue;
        }

        files.push(arg);
    }

    return { files, startNumber, targetLang, titleHint, autoYes };
}

function detectSourceLanguageFromFilename(filePath: string): {
    code?: string;
    label: string;
} {
    const base = path.basename(filePath);
    const match = base.match(/\.([a-z]{2,3})\.srt$/i);
    if (!match) {
        return { label: "wykrywany automatycznie z treści" };
    }

    const code = match[1].toLowerCase();
    const mappedName = LANG_CODE_NAMES[code];
    if (mappedName) {
        return { code, label: `${mappedName} (${code})` };
    }
    return { code, label: `kod '${code}' (z nazwy pliku)` };
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function createPromptInterface(): readline.Interface {
    return readline.createInterface({
        input: process.stdin,
        output: process.stdout,
    });
}

function askQuestion(rl: readline.Interface, query: string): Promise<string> {
    return new Promise((resolve) => {
        rl.question(query, (answer) => resolve(answer));
    });
}

function getErrorStatusCode(err: unknown): number | undefined {
    if (!err || typeof err !== "object") return undefined;
    const anyErr = err as { status?: unknown; code?: unknown; message?: unknown };
    if (typeof anyErr.status === "number") return anyErr.status;
    if (typeof anyErr.code === "number") return anyErr.code;
    if (typeof anyErr.message === "string") {
        const match = anyErr.message.match(/\b(400|401|403|404|409|429|500|502|503|504)\b/);
        if (match) return Number(match[1]);
    }
    return undefined;
}

function modelSortKey(modelName: string): [number, number, number, string] {
    const lower = modelName.toLowerCase();
    let tier = 3;
    if (lower.includes("flash-lite")) tier = 0;
    else if (lower.includes("flash")) tier = 1;
    else if (lower.includes("pro")) tier = 2;

    const isPreview = lower.includes("preview") || lower.includes("exp") ? 1 : 0;
    const verMatch = lower.match(/gemini-(\d+(?:\.\d+)?)/);
    const version = verMatch ? parseFloat(verMatch[1]) : 0;

    return [tier, isPreview, -version, lower];
}

async function discoverUsableModels(ai: GoogleGenAI): Promise<string[]> {
    console.log("Pobieram listę dostępnych modeli z Gemini API...");
    const discovered: string[] = [];

    const pager = await ai.models.list();
    for await (const model of pager) {
        const rawName = model.name ?? "";
        const shortName = rawName.replace(/^models\//, "");
        const lower = shortName.toLowerCase();

        if (!lower.includes("gemini")) continue;
        if (EXCLUDED_MODEL_SUBSTRINGS.some((bad) => lower.includes(bad))) continue;

        const actions =
            (model as { supportedActions?: unknown; supportedGenerationMethods?: unknown })
                .supportedActions ??
            (model as { supportedActions?: unknown; supportedGenerationMethods?: unknown })
                .supportedGenerationMethods;

        if (Array.isArray(actions) && actions.length > 0) {
            const supportsGenerate = actions.some((a) =>
                String(a).toLowerCase().includes("generatecontent")
            );
            if (!supportsGenerate) continue;
        }

        discovered.push(shortName);
    }

    const uniqueSorted = Array.from(new Set(discovered)).sort((a, b) => {
        const ka = modelSortKey(a);
        const kb = modelSortKey(b);
        for (let i = 0; i < 3; i++) {
            if (ka[i] !== kb[i]) return (ka[i] as number) - (kb[i] as number);
        }
        return ka[3].localeCompare(kb[3]);
    });

    console.log(
        `Wykryto ${uniqueSorted.length} obsługiwanych modeli tekstowych: ${uniqueSorted.join(", ")}`
    );
    return uniqueSorted;
}

function stripCodeFences(rawText: string): string {
    return rawText
        .trim()
        .replace(/^```(?:json)?\s*/i, "")
        .replace(/\s*```$/i, "")
        .trim();
}

async function callGeminiRaw(
    ai: GoogleGenAI,
    systemInstruction: string,
    promptContent: string,
    activeModels: string[],
    maxAttempts = 40
): Promise<string> {
    let modelIdx = 0;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        if (activeModels.length === 0) {
            throw new Error("Brak dostępnych modeli – wszystkie zostały wykluczone.");
        }

        const model = activeModels[modelIdx % activeModels.length];

        try {
            if (REQUEST_DELAY_MS > 0) {
                await sleep(REQUEST_DELAY_MS);
            }

            const response = await ai.models.generateContent({
                model,
                contents: promptContent,
                config: {
                    systemInstruction,
                    temperature: 0.2,
                    responseMimeType: "application/json",
                },
            });

            const text = response.text;
            if (!text || !text.trim()) {
                throw new Error("Pusta odpowiedź z API");
            }

            return text;
        } catch (err: unknown) {
            const status = getErrorStatusCode(err);
            if (status === 404) {
                console.log(
                    `\n[${model} APIError 404] Model niedostępny – usuwam z listy i pomijam oczekiwanie.`
                );
                activeModels.splice(activeModels.indexOf(model), 1);
                continue;
            }

            const waitSec =
                status === 429 || status === 503 ? 4 : Math.min(30, 5 * attempt);
            const errMsg = err instanceof Error ? err.message : String(err);
            console.log(
                `\n[${model} Błąd próba ${attempt}/${maxAttempts}] (${errMsg}) -> przełączam/pauza ${waitSec}s`
            );
            modelIdx++;
            await sleep(waitSec * 1000);
        }
    }

    throw new Error("Nie udało się uzyskać odpowiedzi po wyczerpaniu prób.");
}

async function inferTitleAndKeywords(
    ai: GoogleGenAI,
    activeModels: string[],
    filename: string,
    overrideTitle?: string
): Promise<{ exists: boolean; title: string; keywords: string[] }> {
    const prompt = IDENTIFY_PROMPT_TEMPLATE
        .replace("{filename}", path.basename(filename))
        .replace(
            "{title_hint}",
            overrideTitle && overrideTitle.trim()
                ? overrideTitle.trim()
                : "(brak – odczytaj z nazwy pliku)"
        );

    const raw = await callGeminiRaw(
        ai,
        "Jesteś asystentem rozpoznającym tytuły filmów i seriali oraz ich nazwy własne. Zwracaj wyłącznie poprawny JSON.",
        prompt,
        activeModels,
        15
    );

    const parsed = JSON.parse(stripCodeFences(raw)) as IdentifyResponse;
    const exists = Boolean(parsed.exists);
    const title =
        typeof parsed.title === "string" && parsed.title.trim()
            ? parsed.title.trim()
            : path.basename(filename);

    const keywords: string[] = [];
    const seen = new Set<string>();
    if (Array.isArray(parsed.keywords)) {
        for (const item of parsed.keywords) {
            const str = String(item ?? "").trim();
            if (str && !seen.has(str.toLowerCase())) {
                seen.add(str.toLowerCase());
                keywords.push(str);
            }
        }
    }

    return { exists, title, keywords };
}

async function editKeywordsInteractive(
    rl: readline.Interface,
    initialKeywords: string[]
): Promise<string[]> {
    const keywords = [...initialKeywords];

    while (true) {
        console.log(
            `\nLista słów kluczowych (${keywords.length}): ${
                keywords.length > 0 ? keywords.join(", ") : "(brak)"
            }`
        );
        const rawAction = await askQuestion(
            rl,
            "Zatwierdź Enterem, wpisz '+nazwa' (lub 'nazwa') aby dodać, albo '-nazwa' aby usunąć: "
        );
        const action = rawAction.trim();

        if (!action) {
            return keywords;
        }

        if (action.startsWith("-")) {
            const target = action.slice(1).trim().toLowerCase();
            const beforeLen = keywords.length;
            const filtered = keywords.filter((k) => k.toLowerCase() !== target);
            keywords.length = 0;
            keywords.push(...filtered);
            if (keywords.length === beforeLen) {
                console.log(`Nie znaleziono słowa kluczowego: '${action.slice(1).trim()}'`);
            }
        } else if (action.startsWith("+")) {
            const added = action.slice(1).trim();
            if (added && !keywords.some((k) => k.toLowerCase() === added.toLowerCase())) {
                keywords.push(added);
            }
        } else {
            if (!keywords.some((k) => k.toLowerCase() === action.toLowerCase())) {
                keywords.push(action);
            }
        }
    }
}

async function promptTranslationConfig(
    rl: readline.Interface | null,
    ai: GoogleGenAI,
    activeModels: string[],
    sampleFile: string,
    cliOptions: CliOptions
): Promise<{
    title: string;
    keywords: string[];
    targetLang: string;
    startNumber: number;
}> {
    console.log("\n--- Konfiguracja tłumaczenia ---");

    const detectedSource = detectSourceLanguageFromFilename(sampleFile);
    console.log(`Wykryty język źródłowy: ${detectedSource.label}`);

    let overrideTitle: string | undefined = cliOptions.titleHint;
    let title = "";
    let keywords: string[] = [];

    while (true) {
        console.log(`Analizuję nazwę pliku: ${path.basename(sampleFile)}...`);
        const inferred = await inferTitleAndKeywords(
            ai,
            activeModels,
            sampleFile,
            overrideTitle
        );
        title = inferred.title;
        keywords = inferred.keywords;

        console.log(`\nRozpoznany tytuł: ${title}`);
        console.log(
            `Czy taki tytuł istnieje w bazie wiedzy modelu?: ${
                inferred.exists ? "TAK" : "NIE / NIEPEWNE"
            }`
        );
        console.log(
            `Wygenerowane słowa kluczowe: ${
                keywords.length > 0 ? keywords.join(", ") : "(brak)"
            }`
        );
        console.log(`Pomijane frazy napisowe: ${SKIP_PHRASES.join(", ")}`);

        if (cliOptions.autoYes || !rl) {
            break;
        }

        const decision = (
            await askQuestion(
                rl,
                "Potwierdź tytuł Enterem lub wpisz poprawny tytuł, aby odpytać Gemini ponownie: "
            )
        ).trim();

        if (!decision) {
            break;
        }
        overrideTitle = decision;
    }

    if (!cliOptions.autoYes && rl) {
        keywords = await editKeywordsInteractive(rl, keywords);
    }

    let targetLang = cliOptions.targetLang ?? DEFAULT_TARGET_LANG;
    if (cliOptions.targetLang === undefined && !cliOptions.autoYes && rl) {
        const targetLangAnswer = (
            await askQuestion(
                rl,
                `\nNa jaki język tłumaczymy napisy? [${DEFAULT_TARGET_LANG}]: `
            )
        ).trim();
        targetLang = targetLangAnswer || DEFAULT_TARGET_LANG;
    }

    let startNumber = cliOptions.startNumber ?? 0;
    if (cliOptions.startNumber === undefined && !cliOptions.autoYes && rl) {
        const startAnswer = (
            await askQuestion(
                rl,
                "Od którego numeru napisu (bloku SRT) tłumaczymy plik? [Enter = od początku (0)]: "
            )
        ).trim();

        if (startAnswer) {
            const parsedNum = Number.parseInt(startAnswer, 10);
            if (!Number.isNaN(parsedNum) && parsedNum >= 0) {
                startNumber = parsedNum;
            } else {
                console.log("Nieprawidłowy numer – rozpoczynam od początku (0).");
            }
        }
    }

    return { title, keywords, targetLang, startNumber };
}

function buildSystemInstruction(
    title: string,
    keywords: string[],
    sourceLang: string,
    targetLang: string
): string {
    const cleanTitle = title.trim() || "nieokreślony tytuł";
    const cleanSource = sourceLang.trim() || "wykrywany automatycznie";
    const cleanTarget = targetLang.trim() || DEFAULT_TARGET_LANG;
    const cleanKeywords = keywords.map((k) => k.trim()).filter(Boolean);
    const formattedNouns =
        cleanKeywords.length > 0 ? cleanKeywords.join(", ") : "brak";

    return TRANSLATE_PROMPT_TEMPLATE
        .replace(/\{title\}/g, cleanTitle)
        .replace("{proper_nouns}", formattedNouns)
        .replace(/\{source_lang\}/g, cleanSource)
        .replace(/\{target_lang\}/g, cleanTarget);
}

function parseSrtBlocks(content: string): string[] {
    const normalized = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
    if (!normalized) return [];
    return normalized.split(/\n\n+/);
}

function extractBlockNumber(block: string, fallbackIndex: number): number {
    const firstLine = block.split("\n", 1)[0].trim().replace(/^\ufeff/, "");
    if (/^\d+$/.test(firstLine)) {
        return Number.parseInt(firstLine, 10);
    }
    return fallbackIndex + 1;
}

function isBannerBlock(block: string): boolean {
    return block.includes("Przetłumaczono za pomocą:") && block.includes(PROJECT_URL);
}

function parseTimestampMs(ts: string): number {
    const match = ts.trim().match(/(\d{2}):(\d{2}):(\d{2}),(\d{3})/);
    if (!match) return 0;
    const [h, m, s, ms] = match.slice(1).map(Number);
    return ((h * 3600 + m * 60 + s) * 1000) + ms;
}

function formatTimestampMs(totalMs: number): string {
    const clamped = Math.max(0, totalMs);
    const ms = clamped % 1000;
    const totalSec = Math.floor(clamped / 1000);
    const s = totalSec % 60;
    const totalMin = Math.floor(totalSec / 60);
    const m = totalMin % 60;
    const h = Math.floor(totalMin / 60);

    const pad2 = (n: number) => String(n).padStart(2, "0");
    const pad3 = (n: number) => String(n).padStart(3, "0");
    return `${pad2(h)}:${pad2(m)}:${pad2(s)},${pad3(ms)}`;
}

function extractLastEndMs(blocks: string[]): number {
    let lastEnd = 0;
    for (const block of blocks) {
        if (isBannerBlock(block)) continue;
        const lines = block.split("\n");
        if (lines.length >= 2 && lines[1].includes("-->")) {
            const parts = lines[1].split("-->");
            if (parts.length === 2) {
                const endMs = parseTimestampMs(parts[1]);
                if (endMs > lastEnd) lastEnd = endMs;
            }
        }
    }
    return lastEnd;
}

function buildHeaderBanner(): string {
    return [
        "0",
        "00:00:00,000 --> 00:00:02,000",
        "Przetłumaczono za pomocą: gemini-srt-translator",
        PROJECT_URL,
    ].join("\n");
}

function buildFooterBanner(blocks: string[]): string {
    const lastEndMs = extractLastEndMs(blocks);
    const startMs = lastEndMs + 1000;
    const endMs = startMs + 4000;
    const nextIndex = blocks.length + 1;

    return [
        String(nextIndex),
        `${formatTimestampMs(startMs)} --> ${formatTimestampMs(endMs)}`,
        "Przetłumaczono za pomocą: gemini-srt-translator",
        PROJECT_URL,
    ].join("\n");
}

function wrapWithBanners(bodyBlocks: string[]): string[] {
    const cleanBody = bodyBlocks.filter((b) => !isBannerBlock(b));
    return [buildHeaderBanner(), ...cleanBody, buildFooterBanner(cleanBody)];
}

function shouldSkipLine(line: string): boolean {
    const stripped = line.trim();
    if (!stripped) return true;
    const lower = stripped.toLowerCase();
    return SKIP_PHRASES.some((phrase) => lower.includes(phrase));
}

function loadExistingBlocksByNumber(outputPath: string): Map<number, string> {
    const map = new Map<number, string>();
    if (!fs.existsSync(outputPath)) return map;

    const content = fs.readFileSync(outputPath, "utf-8");
    const existingBlocks = parseSrtBlocks(content).filter((b) => !isBannerBlock(b));

    existingBlocks.forEach((block, idx) => {
        const lines = block.split("\n");
        if (lines.length >= 3 && lines[1].includes("-->")) {
            const num = extractBlockNumber(block, idx);
            map.set(num, block);
        }
    });

    return map;
}

function parseIdMap(rawText: string): Map<number, string> {
    const data = JSON.parse(stripCodeFences(rawText)) as {
        translations?: unknown;
    };
    const items = Array.isArray(data)
        ? data
        : Array.isArray(data.translations)
            ? data.translations
            : null;

    if (!items) {
        throw new Error("Brak listy 'translations' w odpowiedzi");
    }

    const mapping = new Map<number, string>();
    for (const entry of items) {
        if (
            entry &&
            typeof entry === "object" &&
            "id" in entry &&
            "text" in entry
        ) {
            const obj = entry as TranslationItem;
            const idNum = Number(obj.id);
            if (!Number.isNaN(idNum)) {
                mapping.set(idNum, String(obj.text));
            }
        }
    }
    return mapping;
}

async function callGeminiBatch(
    ai: GoogleGenAI,
    systemInstruction: string,
    activeModels: string[],
    lines: string[],
    maxAttempts = 40
): Promise<string[]> {
    const payloadItems: TranslationItem[] = lines.map((text, id) => ({
        id,
        text,
    }));
    const payload = JSON.stringify({ items: payloadItems });
    let modelIdx = 0;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        if (activeModels.length === 0) {
            console.log(
                "\n[Brak dostępnych modeli] Wszystkie modele z listy zostały wykluczone."
            );
            break;
        }

        const model = activeModels[modelIdx % activeModels.length];

        try {
            if (REQUEST_DELAY_MS > 0) {
                await sleep(REQUEST_DELAY_MS);
            }

            const response = await ai.models.generateContent({
                model,
                contents: payload,
                config: {
                    systemInstruction,
                    temperature: 0.2,
                    responseMimeType: "application/json",
                },
            });

            const rawText = response.text;
            if (!rawText || !rawText.trim()) {
                throw new Error("Pusta odpowiedź z API");
            }

            const idMap = parseIdMap(rawText);
            const missing: number[] = [];
            for (let idx = 0; idx < lines.length; idx++) {
                if (!idMap.has(idx)) missing.push(idx);
            }

            if (missing.length > 0) {
                throw new Error(`Brakujące ID w odpowiedzi: ${missing.join(", ")}`);
            }

            return lines.map((_, idx) => idMap.get(idx) ?? lines[idx]);
        } catch (err: unknown) {
            const status = getErrorStatusCode(err);
            if (status === 404) {
                console.log(
                    `\n[${model} APIError 404] Model niedostępny – usuwam z listy i pomijam oczekiwanie.`
                );
                activeModels.splice(activeModels.indexOf(model), 1);
                continue;
            }

            const waitSec =
                status === 429 || status === 503 ? 4 : Math.min(30, 5 * attempt);
            const errMsg = err instanceof Error ? err.message : String(err);
            console.log(
                `\n[${model} Błąd paczki próba ${attempt}/${maxAttempts}] (${errMsg}) -> przełączam/pauza ${waitSec}s`
            );
            modelIdx++;
            await sleep(waitSec * 1000);
        }
    }

    if (lines.length > 1 && activeModels.length > 0) {
        console.log("\n[Fallback] Rozbijam paczkę na połówki...");
        const mid = Math.floor(lines.length / 2);
        const left = await callGeminiBatch(
            ai,
            systemInstruction,
            activeModels,
            lines.slice(0, mid),
            8
        );
        const right = await callGeminiBatch(
            ai,
            systemInstruction,
            activeModels,
            lines.slice(mid),
            8
        );
        return [...left, ...right];
    }

    return lines;
}

function buildOutputPath(inputPath: string, targetLang: string): string {
    const lowerLang = targetLang.trim().toLowerCase();
    let suffix = ".translated.srt";
    if (["polski", "polish", "pl"].includes(lowerLang)) {
        suffix = ".pl.srt";
    } else if (/^[a-z]{2,3}$/.test(lowerLang)) {
        suffix = `.${lowerLang}.srt`;
    }

    // Jeśli nazwa kończy się na np. .en.srt (2-3 litery przed .srt), usuwamy ten kod języka źródłowego
    const baseWithoutSrt = inputPath.endsWith(".srt")
        ? inputPath.slice(0, -4).replace(/\.[a-z]{2,3}$/i, "")
        : inputPath.replace(/\.[a-z]{2,3}$/i, "");

    return `${baseWithoutSrt}${suffix}`;
}

function writeSrtFile(outputPath: string, bodyBlocks: string[]): void {
    const finalBlocks = wrapWithBanners(bodyBlocks);
    fs.writeFileSync(outputPath, finalBlocks.join("\n\n") + "\n", "utf-8");
}

async function translateSrt(
    ai: GoogleGenAI,
    systemInstruction: string,
    activeModels: string[],
    targetLang: string,
    inputPath: string,
    startNumber = 0
): Promise<void> {
    const outputPath = buildOutputPath(inputPath, targetLang);
    console.log(`\n--- Tłumaczenie: ${inputPath} -> ${outputPath} ---`);

    const rawContent = fs.readFileSync(inputPath, "utf-8");
    const rawBlocks = parseSrtBlocks(rawContent).filter((b) => !isBannerBlock(b));
    const total = rawBlocks.length;

    const outputBlocks: (string | null)[] = new Array<string | null>(total).fill(null);
    const existingByNum = loadExistingBlocksByNumber(outputPath);

    if (startNumber > 1) {
        let keptCount = 0;
        for (let i = 0; i < total; i++) {
            const blockNum = extractBlockNumber(rawBlocks[i], i);
            if (blockNum < startNumber) {
                if (existingByNum.has(blockNum)) {
                    outputBlocks[i] = existingByNum.get(blockNum)!;
                    keptCount++;
                } else {
                    outputBlocks[i] = rawBlocks[i];
                }
            }
        }
        console.log(
            `Zachowano ${keptCount} przetłumaczonych bloków sprzed numeru ${startNumber}. ` +
            `Tłumaczenie startuje od bloku #${startNumber}.`
        );
    } else {
        console.log("Rozpoczynam tłumaczenie od początku (blok #1).");
    }

    const pendingItems: PendingLine[] = [];
    const parsedBlocks: ParsedSrtBlock[] = [];

    for (let i = 0; i < total; i++) {
        const lines = rawBlocks[i].split("\n");
        if (lines.length < 3 || !lines[1].includes("-->")) {
            parsedBlocks.push({ indexLine: null, timeLine: null, textLines: [] });
            if (outputBlocks[i] === null) {
                outputBlocks[i] = rawBlocks[i];
            }
            continue;
        }

        const indexLine = lines[0].trim();
        const timeLine = lines[1].trim();
        const textLines = lines.slice(2);
        parsedBlocks.push({ indexLine, timeLine, textLines });

        if (outputBlocks[i] === null) {
            textLines.forEach((line, tIdx) => {
                const stripped = line.trim();
                if (shouldSkipLine(stripped)) return;

                const hasItalics =
                    stripped.startsWith("<i>") &&
                    stripped.endsWith("</i>") &&
                    stripped.length > 7;
                const innerText = hasItalics ? stripped.slice(3, -4) : stripped;

                pendingItems.push({
                    blockIndex: i,
                    textLineIndex: tIdx,
                    hasItalics,
                    innerText,
                });
            });
        }
    }

    const initialMaterialized = outputBlocks.map((b, idx) => b ?? rawBlocks[idx]);
    writeSrtFile(outputPath, initialMaterialized);

    if (pendingItems.length === 0) {
        console.log("Brak linii do przetłumaczenia.");
        return;
    }

    const translatedMap = new Map<string, string>();

    for (let start = 0; start < pendingItems.length; start += BATCH_SIZE) {
        const chunk = pendingItems.slice(start, start + BATCH_SIZE);
        const translatedTexts = await callGeminiBatch(
            ai,
            systemInstruction,
            activeModels,
            chunk.map((item) => item.innerText)
        );

        const touchedBlocks = new Set<number>();

        chunk.forEach((item, idx) => {
            const trText = translatedTexts[idx] ?? item.innerText;
            const finalLine = item.hasItalics ? `<i>${trText}</i>` : trText;
            translatedMap.set(`${item.blockIndex}:${item.textLineIndex}`, finalLine);
            touchedBlocks.add(item.blockIndex);
        });

        for (const bIdx of touchedBlocks) {
            const parsed = parsedBlocks[bIdx];
            if (parsed.indexLine !== null && parsed.timeLine !== null) {
                const newTextLines = parsed.textLines.map(
                    (origLine, tIdx) =>
                        translatedMap.get(`${bIdx}:${tIdx}`) ?? origLine
                );
                outputBlocks[bIdx] = [
                    parsed.indexLine,
                    parsed.timeLine,
                    ...newTextLines,
                ].join("\n");
            }
        }

        const materialized = outputBlocks.map((b, idx) => b ?? rawBlocks[idx]);
        writeSrtFile(outputPath, materialized);

        const processedSoFar = Math.min(start + BATCH_SIZE, pendingItems.length);
        console.log(
            `Postęp linii: ${processedSoFar}/${pendingItems.length} (zapisano ${outputPath})`
        );
    }

    console.log(`Zakończono: ${outputPath}`);
}

function findSrtFilesInCwd(): string[] {
    const entries = fs.readdirSync(process.cwd(), { withFileTypes: true });
    return entries
        .filter(
            (e) =>
                e.isFile() &&
                e.name.toLowerCase().endsWith(".srt") &&
                !e.name.toLowerCase().endsWith(".pl.srt") &&
                !e.name.toLowerCase().endsWith(".translated.srt")
        )
        .map((e) => e.name)
        .sort((a, b) => a.localeCompare(b));
}

async function main(): Promise<void> {
    if (!process.env.GEMINI_API_KEY) {
        console.error("Błąd: brak zmiennej środowiskowej GEMINI_API_KEY.");
        process.exit(1);
    }

    const cliOptions = parseCliArgs(process.argv.slice(2));
    const files =
        cliOptions.files.length > 0 ? cliOptions.files : findSrtFilesInCwd();

    if (files.length === 0) {
        console.log("Brak plików .srt do przetłumaczenia.");
        return;
    }

    const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
    const activeModels = await discoverUsableModels(ai);

    if (activeModels.length === 0) {
        console.error(
            "Błąd: nie znaleziono żadnych obsługiwanych modeli Gemini na tym kluczu API."
        );
        process.exit(1);
    }

    const needInteractive =
        !cliOptions.autoYes &&
        (cliOptions.targetLang === undefined || cliOptions.startNumber === undefined || true);

    const rl = needInteractive ? createPromptInterface() : null;
    let config: {
        title: string;
        keywords: string[];
        targetLang: string;
        startNumber: number;
    };

    try {
        config = await promptTranslationConfig(
            rl,
            ai,
            activeModels,
            files[0],
            cliOptions
        );
    } finally {
        rl?.close();
    }

    for (let i = 0; i < files.length; i++) {
        const fileStart = i === 0 ? config.startNumber : 0;
        const fileSourceLang = detectSourceLanguageFromFilename(files[i]).label;
        const systemInstruction = buildSystemInstruction(
            config.title,
            config.keywords,
            fileSourceLang,
            config.targetLang
        );

        await translateSrt(
            ai,
            systemInstruction,
            activeModels,
            config.targetLang,
            files[i],
            fileStart
        );
    }
}

main().catch((err: unknown) => {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`Krytyczny błąd programu: ${msg}`);
    process.exit(1);
});