const SMALL_NUMBERS = [
  "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
  "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
  "eighteen", "nineteen",
];

const TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];
const SCALES = ["", "thousand", "million", "billion", "trillion"];

export const DEFAULT_PREFERRED_VOICES = [
  "Microsoft Andrew Multilingual Online (Natural)",
  "Microsoft Ava Multilingual Online (Natural)",
  "Microsoft Brian Multilingual Online (Natural)",
  "Microsoft Ava Online (Natural)",
  "Microsoft Emma Online (Natural)",
  "Microsoft Andrew Online (Natural)",
  "Microsoft Brian Online (Natural)",
  "Microsoft Aria Online (Natural)",
];

export function splitForSpeech(text, sentencePauseMs = 140, paragraphPauseMs = 300) {
  if (!text) return [];
  const paragraphs = String(text).split(/\n\s*\n+/).map((part) => part.trim()).filter(Boolean);
  const segmenter = typeof Intl !== "undefined" && Intl.Segmenter
    ? new Intl.Segmenter("en-US", { granularity: "sentence" })
    : null;

  return paragraphs.flatMap((paragraph, paragraphIndex) => {
    const sentences = segmenter
      ? [...segmenter.segment(paragraph)].map(({ segment }) => segment.trim()).filter(Boolean)
      : (paragraph.match(/[^.!?]+(?:[.!?]+|$)/g) || [paragraph]).map((part) => part.trim()).filter(Boolean);
    return sentences.map((sentence, sentenceIndex) => ({
      text: sentence,
      pauseAfterMs: sentenceIndex === sentences.length - 1 && paragraphIndex < paragraphs.length - 1
        ? paragraphPauseMs
        : sentencePauseMs,
    }));
  });
}

function underThousandToWords(value) {
  const words = [];
  let number = value;
  if (number >= 100) {
    words.push(SMALL_NUMBERS[Math.floor(number / 100)], "hundred");
    number %= 100;
  }
  if (number >= 20) {
    words.push(TENS[Math.floor(number / 10)]);
    number %= 10;
  }
  if (number > 0) words.push(SMALL_NUMBERS[number]);
  return words.join(" ");
}

function integerToWords(value) {
  if (!Number.isFinite(value)) return "";
  const integer = Math.trunc(Math.abs(value));
  if (integer === 0) return SMALL_NUMBERS[0];
  const words = [];
  let number = integer;
  let scaleIndex = 0;
  while (number > 0 && scaleIndex < SCALES.length) {
    const chunk = number % 1000;
    if (chunk) {
      const scale = SCALES[scaleIndex];
      words.unshift([underThousandToWords(chunk), scale].filter(Boolean).join(" "));
    }
    number = Math.floor(number / 1000);
    scaleIndex += 1;
  }
  return `${value < 0 ? "minus " : ""}${words.join(" ")}`;
}

function numberToWords(value, maximumDecimals = 2) {
  const numeric = Number(String(value).replaceAll(",", ""));
  if (!Number.isFinite(numeric)) return String(value);
  const rounded = Number(numeric.toFixed(maximumDecimals));
  const [integerPart, decimalPart] = Math.abs(rounded).toString().split(".");
  const words = [integerToWords(Number(integerPart))];
  if (decimalPart) {
    words.push("point", ...decimalPart.split("").map((digit) => SMALL_NUMBERS[Number(digit)]));
  }
  return `${rounded < 0 ? "minus " : ""}${words.join(" ")}`.replace(/^minus minus /, "minus ");
}

export function sanitizeForSpeech(text) {
  if (!text) return "";
  let cleaned = String(text)
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/^#{1,6}\s*/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/\[(.*?)\]\((.*?)\)/g, "$1");

  cleaned = cleaned
    .replace(
      /(?:last\s+(?:update|updated|refresh|pull)|cache\s+update|timestamp)\s*:\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?/gi,
      "updated recently",
    )
    .replace(
      /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b/gi,
      "updated recently",
    )
    .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/gi, " ")
    .replace(/(\d+)\s*[Yy]\s*[-\u2013\u2014]\s*(\d+)\s*[Yy]\b/g, (_, first, second) => (
      `${numberToWords(first, 0)}-year minus ${numberToWords(second, 0)}-year`
    ))
    .replace(
      /\$\s*(-?\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|trillion)\b(?:\s*USD)?/gi,
      (_, value, scale) => {
        const wholeMagnitude = String(value).split(".")[0];
        return `${numberToWords(wholeMagnitude, 0)} ${scale.toLowerCase()} dollars`;
      },
    )
    .replace(/\$\s*(-?\d[\d,]*(?:\.\d+)?)\s*([KMBT])\b/gi, (_, value, suffix) => {
      const scales = { K: "thousand", M: "million", B: "billion", T: "trillion" };
      const wholeMagnitude = String(value).split(".")[0];
      return `${numberToWords(wholeMagnitude, 0)} ${scales[suffix.toUpperCase()]} dollars`;
    })
    .replace(/\$\s*(-?\d[\d,]*(?:\.\d+)?)/g, (_, value) => `${numberToWords(value)} dollars`)
    .replace(/(-?\d[\d,]*(?:\.\d+)?)\s*%/g, (_, value) => `${numberToWords(value)} percent`)
    .replace(/\b(-?\d[\d,]*\.\d+)\b/g, (_, value) => numberToWords(value, 3))
    .replace(/\b(\d+)\s*[Yy]\b/g, (_, value) => `${numberToWords(value, 0)}-year`)
    .replace(/[\\/()[\]{}]+/g, " ")
    .replace(/[|>`~*_#]+/g, " ")
    .replace(/[.;:!?]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  return cleaned;
}

export function selectPreferredVoices(
  voices,
  configuredVoice = "",
  preferredNames = DEFAULT_PREFERRED_VOICES,
) {
  if (!Array.isArray(voices) || voices.length === 0) return [];

  const result = [];
  const seen = new Set();
  const add = (voice) => {
    if (!voice) return;
    const key = `${voice.voiceURI || voice.name}|${voice.lang || ""}`;
    if (seen.has(key)) return;
    seen.add(key);
    result.push(voice);
  };
  const exact = (name) => voices.find(
    (voice) => String(voice.name || "").toLowerCase() === String(name || "").toLowerCase(),
  );

  if (configuredVoice && configuredVoice !== "browser-default") add(exact(configuredVoice));
  preferredNames.forEach((name) => add(exact(name)));
  voices
    .filter((voice) => String(voice.lang || "").toLowerCase() === "en-us")
    .filter((voice) => /natural/i.test(String(voice.name || "")))
    .forEach(add);
  voices
    .filter((voice) => String(voice.lang || "").toLowerCase() === "en-us")
    .forEach(add);
  voices
    .filter((voice) => String(voice.lang || "").toLowerCase().startsWith("en-"))
    .forEach(add);
  voices.forEach(add);
  return result;
}

export function selectPreferredVoice(voices, configuredVoice, preferredNames) {
  return selectPreferredVoices(voices, configuredVoice, preferredNames)[0] || null;
}
