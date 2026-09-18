import { AxiosError, type AxiosResponse } from "axios";
import { describe, expect, it } from "vitest";

import { errorMessage } from "./http";

/**
 * errorMessage() is the only thing standing between a platform failure and a
 * useful message in the console, so it is tested against the real payloads --
 * captured from a live run, not invented here.
 */
const failing = (status: number, data: unknown): AxiosError => {
  const response = { status, statusText: "Error", data, headers: {}, config: {} } as AxiosResponse;
  return new AxiosError(
    `Request failed with status code ${status}`,
    "ERR_BAD_RESPONSE",
    {},
    {},
    response,
  );
};

describe("errorMessage", () => {
  it("reads FastAPI's detail string", () => {
    expect(errorMessage(failing(401, { detail: "Trusted identity proxy authentication required" }), "x"))
      .toBe("Trusted identity proxy authentication required");
  });

  it("names the permission a 403 demands", () => {
    expect(errorMessage(failing(403, { detail: "Permission required: asset.write" }), "x"))
      .toBe("Permission required: asset.write");
  });

  it("reads the platform domain envelope, including its code", () => {
    const body = {
      error: {
        code: "SECRET_NOT_FOUND",
        message: "Secret reference was not found",
        details: {},
        trace_id: "56879e7d",
      },
    };
    // Before this the operator saw "Request failed with status code 404".
    expect(errorMessage(failing(404, body), "x")).toBe("Secret reference was not found（SECRET_NOT_FOUND）");
  });

  it("turns validation errors into the fields that failed", () => {
    const body = {
      detail: [
        { type: "missing", loc: ["body", "value"], msg: "Field required" },
        { type: "enum", loc: ["body", "asset_type"], msg: "Input should be 'DOMAIN' or 'IP'" },
      ],
    };
    expect(errorMessage(failing(422, body), "x")).toBe(
      "value: Field required；asset_type: Input should be 'DOMAIN' or 'IP'",
    );
  });

  it("never renders an object as text", () => {
    const message = errorMessage(failing(422, { detail: [{ loc: ["body"], msg: "Invalid" }] }), "x");
    expect(message).toBe("Invalid");
    expect(message).not.toContain("[object Object]");
  });

  it("falls back when the platform said nothing", () => {
    expect(errorMessage(failing(502, "upstream rejected"), "加载失败")).toBe(
      "Request failed with status code 502",
    );
    expect(errorMessage(new Error("network down"), "加载失败")).toBe("network down");
    expect(errorMessage(undefined, "加载失败")).toBe("加载失败");
  });
});
