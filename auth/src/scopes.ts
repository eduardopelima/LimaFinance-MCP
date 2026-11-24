export const SCOPE_READ = "mcp:read";
export const SCOPE_WRITE = "mcp:write";

/** Props gravadas (criptografadas) no grant. O Worker Python resolve/cria o usuário pelo e-mail. */
export interface AuthProps {
  email: string;
  name?: string;
}
