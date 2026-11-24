import {
  AuthorizationError,
  CimdFetchError,
  authorizationErrorRedirect,
  type AuthRequest,
  type ConsentDescription,
  type OAuthHelpers,
} from "@cloudflare/workers-oauth-provider";
import { exchangeGoogleCode, googleAuthorizeUrl, pkcePair } from "./google";
import { SCOPE_READ, SCOPE_WRITE, type AuthProps } from "./scopes";

const esc = (v: string) => v.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

export function isAllowed(env: Env, email: string): boolean {
  const allow = env.ALLOWED_EMAILS.split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);
  return allow.includes(email.trim().toLowerCase());
}

/** Login de desenvolvimento: exige DEV_LOGIN=true E host loopback. Nunca ativo em *.workers.dev. */
function devLoginEnabled(env: Env, request: Request): boolean {
  const host = new URL(request.url).hostname;
  return env.DEV_LOGIN === "true" && (host === "localhost" || host === "127.0.0.1" || host === "[::1]");
}

/** UI do authorization server: /authorize (consentimento + login) e callback do Google. */
export async function authHandler(request: Request, env: Env, oauth: OAuthHelpers): Promise<Response> {
  const url = new URL(request.url);
  try {
    if (url.pathname === "/authorize" && request.method === "GET") return await showConsent(request, env, oauth);
    if (url.pathname === "/authorize" && request.method === "POST") return await submitConsent(request, env, oauth);
    if (url.pathname === "/oauth/google/callback") return await googleCallback(request, env, oauth);
    if (url.pathname === "/health") return Response.json({ ok: true });
    return html("<h1>limafinance · auth</h1><p>Servidor de autorização. Conecte seu cliente de IA ao endpoint MCP.</p>");
  } catch (error) {
    if (error instanceof AuthorizationError && error.redirectTo) return Response.redirect(error.redirectTo, 302);
    if (error instanceof AuthorizationError || error instanceof CimdFetchError) {
      const msg = error instanceof AuthorizationError ? error.description : "Não foi possível verificar este app.";
      return html(`<h1>Não foi possível autorizar</h1><p>${esc(msg ?? "Erro")}</p><p>Volte ao app e tente conectar de novo.</p>`, 400);
    }
    throw error;
  }
}

async function showConsent(request: Request, env: Env, oauth: OAuthHelpers): Promise<Response> {
  const authReq = await oauth.parseAuthRequest(request);
  const details = await oauth.describeConsent(authReq); // antes de beginConsent: falha não deixa lixo no KV
  const consent = await oauth.beginConsent(authReq);
  consent.headers.set("Content-Type", "text/html; charset=utf-8");
  return new Response(consentPage(details, consent.handle, devLoginEnabled(env, request), !!env.GOOGLE_CLIENT_ID), {
    headers: consent.headers,
  });
}

async function submitConsent(request: Request, env: Env, oauth: OAuthHelpers): Promise<Response> {
  const form = await request.formData();
  const handle = String(form.get("handle") ?? "");
  const method = String(form.get("method") ?? "");

  if (method === "deny") {
    const denied = await oauth.denyConsent(request, handle);
    return new Response(null, { status: 302, headers: denied.headers });
  }

  const ticked = form.getAll("scope").map(String).filter((s) => s === SCOPE_WRITE);
  const approved = await oauth.approveConsent(request, handle, {
    scope: [SCOPE_READ, ...ticked, "offline_access"],
  });

  if (method === "dev") {
    if (!devLoginEnabled(env, request)) return html("<h1>Login de desenvolvimento desativado</h1>", 403);
    const email = String(form.get("email") ?? "").trim();
    return finish(oauth, approved.request, email, undefined, approved.headers, env);
  }

  if (method === "google") {
    if (!env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET) return html("<h1>Login Google não configurado</h1>", 500);
    const { verifier, challenge } = await pkcePair();
    const { state, headers } = await oauth.beginUpstream(approved.request, {
      data: { verifier },
      headers: approved.headers,
    });
    headers.set("Location", googleAuthorizeUrl(env, request, state, challenge));
    return new Response(null, { status: 302, headers });
  }
  return html("<h1>Requisição inválida</h1>", 400);
}

async function googleCallback(request: Request, env: Env, oauth: OAuthHelpers): Promise<Response> {
  const { request: original, data, headers } = await oauth.finishUpstream<{ verifier: string }>(request);
  const params = new URL(request.url).searchParams;
  const code = params.get("code");
  if (params.get("error") || !code) {
    headers.set("Location", authorizationErrorRedirect(original, "access_denied"));
    return new Response(null, { status: 302, headers });
  }
  const google = await exchangeGoogleCode(env, request, code, data.verifier);
  return finish(oauth, original, google.email, google.name, headers, env);
}

async function finish(
  oauth: OAuthHelpers,
  authReq: AuthRequest,
  email: string,
  name: string | undefined,
  headers: Headers,
  env: Env,
): Promise<Response> {
  if (!email || !isAllowed(env, email)) {
    return html(
      `<h1>Acesso não liberado</h1><p>A conta <strong>${esc(email || "(vazia)")}</strong> não está autorizada neste servidor.</p>`,
      403,
      headers,
    );
  }
  const normalized = email.trim().toLowerCase();
  const props: AuthProps = { email: normalized, name };
  const { redirectTo } = await oauth.completeAuthorization({
    request: authReq,
    // userId não pode conter ":" (separador interno da lib).
    userId: encodeURIComponent(normalized),
    metadata: { email: normalized },
    scope: authReq.scope,
    props,
  });
  headers.set("Location", redirectTo);
  return new Response(null, { status: 302, headers });
}

function consentPage(d: ConsentDescription, handle: string, dev: boolean, google: boolean): string {
  const origin = d.clientDomain
    ? `Publicado por <strong>${esc(d.clientDomain)}</strong>.`
    : "Este app se registrou sozinho; o nome não é verificado.";
  const loopback = d.redirectIsLoopback
    ? `<p class="warn">⚠️ O acesso será enviado a um app no seu computador (${esc(d.redirectHost)}). Continue só se você acabou de iniciar a conexão nele.</p>`
    : "";
  return page(
    `Autorizar ${esc(d.clientName)}`,
    `<h1>Permitir que <em>${esc(d.clientName)}</em> acesse suas finanças?</h1>
<p>${origin} O acesso será enviado para <strong>${esc(d.redirectHost)}</strong>.</p>
${loopback}
<form method="post">
  <input type="hidden" name="handle" value="${esc(handle)}">
  <fieldset><legend>Permissões</legend>
    <label><input type="checkbox" checked disabled> Ler lançamentos, categorias e resumos</label><br>
    <label><input type="checkbox" name="scope" value="${SCOPE_WRITE}" checked> Registrar, editar e excluir lançamentos</label>
  </fieldset>
  ${google ? `<p><button name="method" value="google" class="primary">Entrar com Google e permitir</button></p>` : ""}
  ${dev ? `<fieldset class="dev"><legend>Login de desenvolvimento (só localhost)</legend>
    <input type="email" name="email" placeholder="email da allowlist">
    <button name="method" value="dev">Entrar (dev) e permitir</button></fieldset>` : ""}
  <p><button name="method" value="deny">Negar</button></p>
</form>`,
  );
}

function page(title: string, body: string): string {
  return `<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title}</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:3rem auto;padding:0 1rem;color:#1a1a1a}
fieldset{border:1px solid #ccc;border-radius:8px;margin:1rem 0}button{font:inherit;padding:.5rem 1rem;border-radius:6px;border:1px solid #888;background:#fff;cursor:pointer}
.primary{background:#1a1a1a;color:#fff;border-color:#1a1a1a}.warn{background:#fff4e0;padding:.5rem;border-radius:6px}.dev{border-style:dashed}
@media (prefers-color-scheme:dark){body{background:#151515;color:#eee}button{background:#222;color:#eee}.primary{background:#eee;color:#111}.warn{background:#3a2a10}}</style>
${body}</html>`;
}

function html(body: string, status = 200, headers?: Headers): Response {
  const h = new Headers(headers);
  h.delete("Location");
  h.set("Content-Type", "text/html; charset=utf-8");
  return new Response(page("limafinance", body), { status, headers: h });
}
