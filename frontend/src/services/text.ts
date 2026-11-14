/** Text helpers for what the chat shows. Stored messages are never changed. */

// Emoji only: pictographs with emoji presentation (with skin tones and joined sequences), symbols
// forced into emoji style with U+FE0F, and flag pairs. Plain symbols such as (c), arrows and check
// marks, and joiners inside other scripts, are left alone.
const EMOJI = new RegExp(
  [
    "\\p{Emoji_Presentation}(?:\\uFE0F|[\\u{1F3FB}-\\u{1F3FF}])*(?:\\u200D\\p{Emoji_Presentation}\\uFE0F?)*",
    "\\p{Extended_Pictographic}\\uFE0F",
    "[\\u{1F1E6}-\\u{1F1FF}]{2}",
  ].join("|"),
  "gu"
);

// Code (fenced or inline) is left exactly as written.
const CODE = /(```[\s\S]*?```|`[^`\n]*`)/;

/** Remove emoji from a model reply for display; code blocks and inline code are untouched. */
export function stripEmoji(text: string): string {
  if (!text) return text;
  return text
    .split(CODE)
    .map((part, i) => (i % 2 === 1 ? part : part.replace(EMOJI, "").replace(/[ \t]{2,}/g, " ").replace(/^[ \t]+(?=\S)/gm, (m) => m)))
    .join("");
}
