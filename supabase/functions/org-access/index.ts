import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const configuredAppBase = (Deno.env.get("APP_BASE_URL") ?? "").trim().replace(/\/$/, "");
let appOrigin = "";
try {
  if (configuredAppBase) {
    const parsed = new URL(configuredAppBase);
    if (parsed.protocol === "https:" || parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1") appOrigin = parsed.origin;
  }
} catch { appOrigin = ""; }

const corsHeadersFor = (origin: string | null) => {
  const headers: Record<string, string> = {
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Vary": "Origin",
  };
  if (origin && appOrigin && origin === appOrigin) headers["Access-Control-Allow-Origin"] = appOrigin;
  return headers;
};
const json = (data: unknown, status = 200, origin: string | null = null) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { ...corsHeadersFor(origin), "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
const sha256 = async (value: string) => {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
};

Deno.serve(async (req) => {
  const origin = req.headers.get("Origin");
  if (req.method === "OPTIONS") {
    if (!origin || !appOrigin || origin !== appOrigin) return new Response("origin_not_allowed", { status: 403, headers: corsHeadersFor(origin) });
    return new Response("ok", { headers: corsHeadersFor(origin) });
  }
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405, origin);
  if (origin && (!appOrigin || origin !== appOrigin)) return json({ error: "origin_not_allowed" }, 403, origin);

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY");
  if (!supabaseUrl || !serviceKey || !anonKey) return json({ error: "server_not_configured" }, 500, origin);
  const authHeader = req.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) return json({ error: "authentication_required" }, 401, origin);
  const token = authHeader.slice(7);
  const userClient = createClient(supabaseUrl, anonKey, { global: { headers: { Authorization: authHeader } }, auth: { persistSession: false, autoRefreshToken: false } });
  const { data: userData, error: userError } = await userClient.auth.getUser(token);
  if (userError || !userData.user) return json({ error: "invalid_session" }, 401, origin);
  const user = userData.user;
  const admin = createClient(supabaseUrl, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } });
  let body: Record<string, unknown>;
  try {
    const parsed = await req.json();
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return json({ error: "invalid_json" }, 400, origin);
    body = parsed as Record<string, unknown>;
  } catch { return json({ error: "invalid_json" }, 400, origin); }

  if (body.action === "create_org") {
    const name = String(body.name ?? "").trim();
    if (name.length < 2 || name.length > 120) return json({ error: "invalid_organization_name" }, 400, origin);
    const { data: existing, error: existingError } = await admin.from("organization_members").select("organization_id").eq("user_id", user.id).eq("status", "active").limit(1);
    if (existingError) return json({ error: "membership_check_failed" }, 500, origin);
    if (existing?.length) return json({ error: "user_already_belongs_to_organization" }, 409, origin);
    const { data: org, error: orgError } = await admin.from("organizations").insert({ name }).select("id,name").single();
    if (orgError || !org) return json({ error: "organization_create_failed" }, 500, origin);
    const { error: memberError } = await admin.from("organization_members").insert({ organization_id: org.id, user_id: user.id, role: "owner", status: "active" });
    if (memberError) {
      await admin.from("organizations").delete().eq("id", org.id);
      return json({ error: "owner_membership_create_failed" }, 500, origin);
    }
    const { error: auditError } = await admin.from("organization_audit_events").insert({ organization_id: org.id, actor_user_id: user.id, event_type: "organization.created", metadata: { name: org.name } });
    if (auditError) {
      await admin.from("organization_members").delete().eq("organization_id", org.id).eq("user_id", user.id);
      await admin.from("organizations").delete().eq("id", org.id);
      return json({ error: "organization_audit_failed" }, 500, origin);
    }
    return json({ organization: org, role: "owner" }, 201, origin);
  }

  if (body.action === "invite") {
    if (!appOrigin) return json({ error: "app_base_url_not_configured" }, 503, origin);
    const organizationId = String(body.organization_id ?? "");
    const email = String(body.email ?? "").trim().toLowerCase();
    const role = String(body.role ?? "member");
    const days = Number(body.valid_days ?? 7);
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return json({ error: "invalid_email" }, 400, origin);
    if (!["admin", "member", "viewer"].includes(role)) return json({ error: "invalid_role" }, 400, origin);
    if (!Number.isInteger(days) || days < 1 || days > 30) return json({ error: "invalid_valid_days" }, 400, origin);
    const { data: membership, error: membershipError } = await admin.from("organization_members").select("role,status").eq("organization_id", organizationId).eq("user_id", user.id).maybeSingle();
    if (membershipError) return json({ error: "authorization_check_failed" }, 500, origin);
    if (!membership || membership.status !== "active" || !["owner", "admin"].includes(membership.role)) return json({ error: "organization_admin_required" }, 403, origin);
    const rawToken = crypto.randomUUID() + "-" + crypto.randomUUID();
    const tokenHash = await sha256(rawToken);
    const expiresAt = new Date(Date.now() + days * 86400000).toISOString();
    const { data: invite, error: inviteError } = await admin.from("organization_invites").insert({ organization_id: organizationId, email, role, token_hash: tokenHash, created_by: user.id, expires_at: expiresAt }).select("id").single();
    if (inviteError || !invite) return json({ error: "invite_create_failed" }, 500, origin);
    const redirectTo = `${appOrigin}/accept-invite?token=${encodeURIComponent(rawToken)}`;
    const { error: emailError } = await admin.auth.admin.inviteUserByEmail(email, { redirectTo, data: { organization_invite_id: invite.id } });
    if (emailError) {
      await admin.from("organization_invites").delete().eq("id", invite.id);
      return json({ error: "email_invitation_failed" }, 502, origin);
    }
    const { error: auditError } = await admin.from("organization_audit_events").insert({ organization_id: organizationId, actor_user_id: user.id, event_type: "invite.sent", target_email: email, metadata: { invite_id: invite.id, role, expires_at: expiresAt } });
    if (auditError) return json({ error: "invite_audit_failed_contact_admin" }, 500, origin);
    return json({ invite_id: invite.id, email, role, expires_at: expiresAt, email_sent: true }, 201, origin);
  }

  if (body.action === "accept_invite") {
    const rawToken = String(body.token ?? "");
    if (rawToken.length < 20 || rawToken.length > 200) return json({ error: "invalid_invite" }, 400, origin);
    const { data, error } = await userClient.rpc("accept_organization_invite", { p_token_hash: await sha256(rawToken) });
    if (error) {
      const message = error.message ?? "";
      const mismatch = /email_mismatch|42501/.test(message);
      const code = /invite_expired/.test(message) ? "invite_expired"
        : /invite_already_used/.test(message) ? "invite_already_used"
        : /invite_revoked/.test(message) ? "invite_revoked"
        : /invalid_invite/.test(message) ? "invalid_invite"
        : mismatch ? "email_mismatch" : "invite_acceptance_failed";
      return json({ error: code }, mismatch ? 403 : 400, origin);
    }
    return json({ organization_id: data, accepted: true }, 200, origin);
  }
  return json({ error: "unknown_action" }, 400, origin);
});
