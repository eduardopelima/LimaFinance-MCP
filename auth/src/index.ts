import { OAuthAuthorizationServer } from "@cloudflare/workers-oauth-provider";
import { WorkerEntrypoint, env as staticEnv } from "cloudflare:workers";
import { authHandler } from "./handler";
import { SCOPE_READ, SCOPE_WRITE, type AuthProps } from "./scopes";

/**
 * Authorization server (OAuth 2.1) do limafinance. Só cuida de IDENTIDADE:
 * DCR/CIMD, consentimento, login Google, emissão/refresh de tokens. Não acessa dados financeiros.
 * O resource server (Worker Python em /mcp) valida tokens chamando `validateToken` via Service Binding.
 */
const authorizationServer = new OAuthAuthorizationServer<Env>({
  issuer: staticEnv.ISSUER,
  resources: [staticEnv.MCP_RESOURCE],
  defaultResource: staticEnv.MCP_RESOURCE,
  scopesSupported: [SCOPE_READ, SCOPE_WRITE, "offline_access"],
  clientRegistrationEndpoint: "/oauth/register", // DCR: ChatGPT, Gemini CLI, MCP Inspector
  clientIdMetadataDocumentEnabled: true, // CIMD: Claude
  accessTokenTTL: 3600,
  refreshTokenTTL: 90 * 86400,
});

export default class AuthServer extends WorkerEntrypoint<Env> {
  override async fetch(request: Request): Promise<Response> {
    const path = new URL(request.url).pathname;
    // Rotas de UI (nossas). O resto (metadata, token, register, revoke) é do protocolo, servido pela lib.
    if (path === "/" || path === "/health" || path === "/authorize" || path === "/oauth/google/callback") {
      return authHandler(request, this.env, authorizationServer.getOAuthApi(this.env));
    }
    return authorizationServer.fetch(request, this.env, this.ctx);
  }

  /** RPC de prontidão: o /health?deep=1 do Worker Python usa para provar que o Service Binding funciona. */
  ping(): string {
    return "pong";
  }

  /**
   * RPC chamado SÓ pelo Worker Python (Service Binding; não é exposto na internet).
   * Retorna null para token inválido, expirado, revogado ou emitido para outro resource.
   */
  async validateToken(resource: string, token: string) {
    const v = await authorizationServer.validateToken<AuthProps>(resource, token, this.env);
    if (!v) return null;
    return { email: v.props.email, name: v.props.name ?? null, googleSub: v.props.googleSub ?? null, scope: v.scope, clientId: v.clientId, expiresAt: v.expiresAt, audience: v.audience };
  }
}
