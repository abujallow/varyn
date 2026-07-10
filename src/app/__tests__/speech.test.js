import { describe, expect, it } from "vitest";
import {
  DEFAULT_PREFERRED_VOICES,
  sanitizeForSpeech,
  selectPreferredVoices,
  splitForSpeech,
} from "../speech.js";

describe("sanitizeForSpeech", () => {
  it("strips markdown bold/italic/headers/links", () => {
    const out = sanitizeForSpeech("# Title\n**bold** and *italic* and [link](http://x.com)");
    expect(out).not.toMatch(/[*#[\]]/);
    expect(out).toContain("bold");
    expect(out).toContain("italic");
    expect(out).toContain("link");
  });

  it("strips code fences entirely", () => {
    const out = sanitizeForSpeech("before ```const x = 1;``` after");
    expect(out).not.toContain("const x");
  });

  it("replaces ISO timestamps with a spoken phrase", () => {
    const out = sanitizeForSpeech("Last update: 2026-01-05T10:00:00Z");
    expect(out).toContain("updated recently");
    expect(out).not.toContain("2026-01-05");
  });

  it("strips UUIDs", () => {
    const out = sanitizeForSpeech("id 550e8400-e29b-41d4-a716-446655440000 done");
    expect(out).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}/);
  });

  it("converts dollar-million shorthand to spoken words", () => {
    const out = sanitizeForSpeech("Revenue was $1.5 million last quarter.");
    expect(out.toLowerCase()).toContain("million dollars");
  });

  it("converts percent signs to spoken word", () => {
    const out = sanitizeForSpeech("Growth was 12%");
    expect(out.toLowerCase()).toContain("percent");
    expect(out).not.toContain("%");
  });

  it("converts numeric dates to spoken month/day/year, with the year read as words", () => {
    const out = sanitizeForSpeech("Filed on 2026-03-15");
    expect(out).toContain("March");
    expect(out).toContain("fifteenth");
    expect(out).not.toContain("2026");
    expect(out.toLowerCase()).toContain("twenty");
  });

  it("returns empty string for falsy input", () => {
    expect(sanitizeForSpeech("")).toBe("");
    expect(sanitizeForSpeech(null)).toBe("");
  });
});

describe("sanitizeForSpeech spoken dates", () => {
  it("reads an ISO date fully as words (month name, word ordinal, word year)", () => {
    const out = sanitizeForSpeech("Report generated 2026-07-09.");
    expect(out).toContain("July");
    expect(out).toContain("ninth");
    expect(out.toLowerCase()).toContain("twenty");
    expect(out.toLowerCase()).toContain("six");
    expect(out).not.toContain("2026");
    expect(out).not.toContain("07-09");
  });

  it("leaves an already-natural written date untouched", () => {
    const out = sanitizeForSpeech("Filed Jul 9, 2026 with the SEC.");
    expect(out).toContain("Jul 9");
  });

  it("converts a date at the very end of a sentence", () => {
    const out = sanitizeForSpeech("The filing closed on 2026-01-31.");
    expect(out).toContain("January");
    expect(out).toContain("thirty-first");
  });

  it("converts multiple dates in one response independently", () => {
    const out = sanitizeForSpeech("Opened 2026-02-01 and closed 2026-02-28.");
    expect(out).toContain("first");
    expect(out).toContain("twenty-eighth");
    expect(out).not.toContain("2026-02");
  });

  it("leaves an invalid calendar date unchanged", () => {
    const out = sanitizeForSpeech("Reference code 2026-13-45 was logged.");
    expect(out).toContain("2026-13-45");
  });

  it("converts a valid leap day", () => {
    const out = sanitizeForSpeech("Reported on 2024-02-29.");
    expect(out).toContain("February");
    expect(out).toContain("twenty-ninth");
  });

  it("leaves an invalid leap day (non-leap year) unchanged", () => {
    const out = sanitizeForSpeech("Reported on 2023-02-29.");
    expect(out).toContain("2023-02-29");
  });

  it("does not alter a bare four-digit year with no month/day", () => {
    const out = sanitizeForSpeech("Guidance covers fiscal year 2026 only.");
    expect(out).toContain("2026");
  });

  it("does not alter an unrelated decimal number", () => {
    const out = sanitizeForSpeech("The reading was 10.09 today.");
    expect(out).not.toContain("July");
    expect(out).not.toContain("October");
  });

  it("does not alter a date-like sequence embedded in a bare URL path", () => {
    const out = sanitizeForSpeech("See https://www.sec.gov/2026/07/09/filing for the source.");
    expect(out).toContain("2026");
    expect(out).toContain("07");
    expect(out).toContain("09");
    expect(out).not.toContain("July ninth");
  });
});

describe("splitForSpeech", () => {
  it("returns an empty array for falsy input", () => {
    expect(splitForSpeech("")).toEqual([]);
  });

  it("splits on blank lines into paragraphs without dropping text", () => {
    const parts = splitForSpeech("First paragraph.\n\nSecond paragraph.");
    expect(parts.length).toBe(2);
    expect(parts[0].text).toContain("First paragraph");
    expect(parts[1].text).toContain("Second paragraph");
  });

  it("gives every paragraph except the last a nonzero pause", () => {
    const parts = splitForSpeech("One.\n\nTwo.\n\nThree.", 200);
    expect(parts[0].pauseAfterMs).toBe(200);
    expect(parts[1].pauseAfterMs).toBe(200);
    expect(parts[parts.length - 1].pauseAfterMs).toBe(0);
  });

  it("produces non-empty sentence arrays for each paragraph", () => {
    const parts = splitForSpeech("Sentence one. Sentence two.");
    expect(parts[0].sentences.length).toBeGreaterThan(0);
  });
});

describe("selectPreferredVoices", () => {
  const voices = [
    { name: "Microsoft David", lang: "en-US" },
    { name: "Microsoft Andrew Multilingual Online (Natural)", lang: "en-US" },
    { name: "Google UK English", lang: "en-GB" },
    { name: "Google Deutsch", lang: "de-DE" },
  ];

  it("returns empty array for empty/invalid input", () => {
    expect(selectPreferredVoices([])).toEqual([]);
    expect(selectPreferredVoices(null)).toEqual([]);
  });

  it("prioritizes the configured voice first", () => {
    const result = selectPreferredVoices(voices, "Microsoft David");
    expect(result[0].name).toBe("Microsoft David");
  });

  it("prioritizes preferred-name matches over generic en-US voices", () => {
    const result = selectPreferredVoices(voices, "", DEFAULT_PREFERRED_VOICES);
    expect(result[0].name).toBe("Microsoft Andrew Multilingual Online (Natural)");
  });

  it("deduplicates voices by voiceURI/lang", () => {
    const duped = [...voices, voices[0]];
    const result = selectPreferredVoices(duped);
    const names = result.map((v) => v.name);
    expect(names.filter((n) => n === "Microsoft David").length).toBe(1);
  });

  it("includes all voices eventually, even non-English ones", () => {
    const result = selectPreferredVoices(voices);
    expect(result.some((v) => v.name === "Google Deutsch")).toBe(true);
  });
});
