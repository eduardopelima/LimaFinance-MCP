/** Login upstream via Google (OIDC, authorization code + PKCE). Só queremos o e-mail verificado. */

const AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";
const TOKEN_URL = "https://oauth2.googleapis.com/token";

export function callbackUrl(request: Request): string {
  return new URL("/oauth/google/callback", request.url).toString();
}

export async function pkcePair(): Promise<{ verifier: string; challenge: string }> {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return { verifier, challenge: b64url(new Uint8Array(digest)) };
}

export function googleAuthorizeUrl(env: Env, request: Request, state: string, challenge: string): string {
  const u = new URL(AUTH_URL);
  u.search = new URLSearchParams({
    client_id: env.GOOGLE_CLIENT_ID,
    redirect_uri: callbackUrl(request),
    response_type: "code",
    scope: "openid email profile",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
    prompt: "select_account",
  }).toString();
  return u.toString();
}

export async function exchangeGoogleCode(
  env: Env,
  request: Request,
  code: string,
  verifier: string,
): Promise<{ email: string; name?: string }> {
  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: env.GOOGLE_CLIENT_ID,
      client_secret: env.GOOGLE_CLIENT_SECRET,
      redirect_uri: callbackUrl(request),
      grant_type: "authorization_code",
      code_verifier: verifier,
    }),
  });
  if (!res.ok) throw new Error(`google_token_exchange_failed:${res.status}`);
  const body = (await res.json()) as { id_token?: string };
  if (!body.id_token) throw new Error("google_no_id_token");
  // id_token recebido diretamente do token endpoint do Google via TLS: OIDC Core 3.1.3.7
  // permite confiar no canal em vez de validar a assinatura. Ainda checamos iss/aud/exp/email_verified.
  const claims = JSON.parse(new TextDecoder().decode(b64urlDecode(body.id_token.split(".")[1]!))) as {
    iss: string; aud: string; exp: number; email?: string; email_verified?: boolean; name?: string;
  };
  if (!["https://accounts.google.com", "accounts.google.com"].includes(claims.iss)) throw new Error("google_bad_iss");
  if (claims.aud !== env.GOOGLE_CLIENT_ID) throw new Error("google_bad_aud");
  if (claims.exp * 1000 < Date.now()) throw new Error("google_expired");
  if (!claims.email || claims.email_verified !== true) throw new Error("google_email_not_verified");
  return { email: claims.email, name: claims.name };
}

function b64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(s: string): Uint8Array {
  const pad = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  return Uint8Array.from(atob(pad), (c) => c.charCodeAt(0));
}
