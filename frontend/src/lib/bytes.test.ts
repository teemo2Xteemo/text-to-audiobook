import { describe, expect, it } from "vitest";

import { utf8ByteLength } from "./bytes";

describe("utf8ByteLength", () => {
  it("matches TextEncoder for ASCII, CJK, and a surrogate pair", () => {
    for (const text of ["", "hello", "你", "你好", "🙂", "a你🙂"]) {
      expect(utf8ByteLength(text)).toBe(new TextEncoder().encode(text).length);
    }
  });
});
