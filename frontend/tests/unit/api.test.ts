import { beforeEach, describe, expect, it, vi } from "vitest";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const USER = { id: 1, email: "maria@opspilot.dev", full_name: "Maria", role: "engineer", permissions: [], last_login_at: null };

describe("api client", () => {
  beforeEach(() => vi.resetModules());

  it("builds query strings, skipping empty values and repeating arrays", async () => {
    const { query } = await import("@/services/api");
    expect(query({ state: "active", page: 2, q: "", service: null, severity: ["SEV1", "SEV2"] }))
      .toBe("?state=active&page=2&severity=SEV1&severity=SEV2");
    expect(query({})).toBe("");
  });

  it("refreshes once on 401 and retries with the new token", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json(401, { detail: "not authenticated" }))
      .mockResolvedValueOnce(json(200, { access_token: "fresh", expires_in: 900, user: USER }))
      .mockResolvedValueOnce(json(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/services/api");

    await expect(api("/incidents")).resolves.toEqual({ ok: true });
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/api/v1/incidents", "/api/v1/auth/refresh", "/api/v1/incidents"]);
    const retried = fetchMock.mock.calls[2][1].headers as Headers;
    expect(retried.get("Authorization")).toBe("Bearer fresh");
  });

  it("reports an expired session when the refresh fails", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(json(401, { detail: "not authenticated" }))
      .mockResolvedValueOnce(json(401, { detail: "refresh token invalid" })));
    const { api, setSessionExpiredHandler, ApiError } = await import("@/services/api");
    const expired = vi.fn();
    setSessionExpiredHandler(expired);

    await expect(api("/incidents")).rejects.toBeInstanceOf(ApiError);
    expect(expired).toHaveBeenCalledOnce();
  });

  it("surfaces the API's error detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(409, { error: "conflict", detail: "investigation 3 is already in progress" })));
    const { post } = await import("@/services/api");
    await expect(post("/incidents/INC-1/investigations")).rejects.toMatchObject({
      status: 409, message: "investigation 3 is already in progress",
    });
  });
});
