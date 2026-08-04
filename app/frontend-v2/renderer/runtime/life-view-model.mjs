const SHA256 = /^[0-9a-f]{64}$/;
const SAFE_SOURCE = /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$/;

function objectValue(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function textValue(value) {
  return typeof value === "string" ? value.trim() : "";
}

function revisionValue(value, minimum = 0) {
  return Number.isSafeInteger(value) && value >= minimum ? value : null;
}

function sourceValue(value) {
  const items = Array.isArray(value) ? value : [value];
  const normalized = items.map(textValue).filter((item) => SAFE_SOURCE.test(item));
  return normalized.length === items.length && normalized.length ? Object.freeze(normalized) : null;
}

export function normalizeUserIdentity(settings = {}) {
  const source = objectValue(settings);
  const callsign = textValue(source.callsign || source.userCallsign || source.userName || source.userDisplayName) || "你";
  return Object.freeze({
    relationshipId: "user:primary",
    callsign,
    avatarDataUrl: textValue(source.avatarDataUrl || source.userAvatarDataUrl),
    fallbackGlyph: Array.from(callsign)[0] || "你"
  });
}

export function relationshipDisplayName(relationshipId, settings = {}) {
  const identity = normalizeUserIdentity(settings);
  return String(relationshipId || "") === identity.relationshipId
    ? `${identity.callsign} · 主要用户关系`
    : String(relationshipId || "");
}

function authorityProjection(payload) {
  const authority = objectValue(payload?.projection_authority || payload?.authority);
  const revisions = objectValue(authority.revisions);
  const sources = objectValue(authority.source_refs);
  const vectorSha256 = textValue(revisions.vector_sha256);
  if (authority.schema !== "tiangong.gateway.life-view-authority.v1" || !SHA256.test(vectorSha256)) {
    return null;
  }
  const required = {
    writer: revisionValue(revisions.writer_epoch, 1),
    identity: revisionValue(revisions.identity_revision, 1),
    soul: revisionValue(revisions.soul_revision, 1),
    memory: revisionValue(revisions.memory_revision),
    affect: revisionValue(revisions.affect_revision),
    causal: revisionValue(revisions.causal_revision),
    viability: revisionValue(revisions.viability_revision),
    policy: revisionValue(revisions.policy_revision),
    reflection: revisionValue(revisions.reflection_revision),
    capability: revisionValue(revisions.capability_revision)
  };
  if (Object.values(required).some((value) => value === null)) return null;
  const normalizedSources = {};
  for (const [domain, value] of Object.entries(sources)) {
    const normalized = sourceValue(value);
    if (normalized) normalizedSources[domain] = normalized;
  }
  return Object.freeze({ revisions: Object.freeze(required), sourceRefs: Object.freeze(normalizedSources), vectorSha256 });
}

function sourced(authority, domain) {
  return Boolean(authority?.sourceRefs?.[domain]?.length);
}

function baseProjection(payload, authority, status, userIdentity) {
  const sections = objectValue(payload?.sections);
  return {
    ok: payload?.ok !== false,
    setup_required: payload?.setup_required === true,
    generated_at: textValue(payload?.generated_at),
    errors: Array.isArray(payload?.errors) ? payload.errors : [],
    sections,
    projection_status: status,
    projection_authority: authority ? {
      schema: "tiangong.gateway.life-view-authority.v1",
      revisions: authority.revisions,
      source_refs: authority.sourceRefs,
      vector_sha256: authority.vectorSha256
    } : null,
    user_identity: userIdentity,
    identity: {}, identities: [], soul: {}, temperament: {}, summary: {}, state: {}, affect: {}, relationship: {}, body: {}, memory: {}, context: {},
    schedule: {}, inbox: {}, budget: {}, free_will: {}, scheduler: {}, preferences: {}, learning: {},
    boundaries: {}, settings: {}, system_capabilities: {}, evolution: {}, capabilities: {},
    tasks: [], goals: [], drift: [], action_values: [], reflections: [], upgrade_cards: []
  };
}

export function buildLifeViewModel(payload = {}, settings = {}) {
  const raw = objectValue(payload);
  const userIdentity = normalizeUserIdentity(settings);
  let authority = authorityProjection(raw);
  if (!authority) {
    const soul = objectValue(raw.soul);
    const identity = objectValue(raw.identity);
    const hash = textValue(raw.source_hash) || textValue(soul.revision_id);
    const hasLegacyAuthority = Object.keys(identity).length > 0 || Object.keys(soul).length > 0;
    if (hasLegacyAuthority && SHA256.test(hash)) {
      const src = "tiangong-life-service-compat";
      authority = Object.freeze({
        revisions: Object.freeze({
          writer_epoch: revisionValue(raw.writer_epoch, 1)
            || revisionValue(identity.writer_epoch, 1) || 1,
          identity_revision: revisionValue(identity.revision, 1) || 1,
          soul_revision: revisionValue(soul.revision, 1) || 1,
          memory_revision: 0, affect_revision: 0, causal_revision: 0,
          viability_revision: 0, policy_revision: 0, reflection_revision: 0,
          capability_revision: 0,
        }),
        sourceRefs: Object.freeze({
          projection_source: [src], identity: [src], soul: [src],
          temperament: [src], affect: [src], memory: [src], causal: [src],
          viability: [src], policy: [src], reflection: [src], capability: [src],
        }),
        vectorSha256: hash,
      });
    }
  }
  const view = baseProjection(raw, authority, authority ? "authoritative" : "awaiting_authority_projection", userIdentity);
  if (!authority) return Object.freeze(view);

  const copy = (domain, keys) => {
    if (!sourced(authority, domain)) return;
    for (const key of keys) view[key] = raw[key] ?? view[key];
  };
  // P1-09: identity_audit must survive projection; the life panel renders it
  // as the identity operation history.
  copy("identity", ["identity", "identities", "identity_audit"]);
  copy("soul", ["soul"]);
  copy("temperament", ["temperament"]);
  copy("affect", ["affect", "relationship"]);
  copy("memory", ["memory"]);
  copy("causal", ["context"]);
  copy("viability", ["summary", "state", "body", "schedule", "inbox", "budget", "tasks"]);
  if (sourced(authority, "viability") && sourced(authority, "policy")) {
    for (const key of ["free_will", "scheduler", "preferences", "goals", "drift", "action_values"] ) {
      view[key] = raw[key] ?? view[key];
    }
  }
  copy("reflection", ["reflections", "learning"]);
  copy("capability", ["capabilities", "evolution", "system_capabilities", "upgrade_cards"]);
  copy("policy", ["boundaries", "settings"]);
  return Object.freeze(view);
}

export const LIFE_VIEW_AUTHORITY_SCHEMA = "tiangong.gateway.life-view-authority.v1";
