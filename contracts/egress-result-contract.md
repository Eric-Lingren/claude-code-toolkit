# egress-result Contract

**Schema:** `egress-result-schema.json` (JSON Schema draft-07, `schema_version: "1"`)

Every egress channel adapter — copy-only stubs and future live write-back implementations — returns exactly this shape. No adapter may return a different structure. Consumers must validate against the schema before acting on the result.

---

## Field reference

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | `"1"` (const string) | Yes | Hard-cutover version. Reject any document with an unrecognized value. |
| `posted` | `boolean` | Yes | `true` if the message was delivered to the channel. `false` for copy-only stubs and failed adapters. |
| `url` | `string \| null` | No | Permalink to the posted message. `null` for copy-only stubs and failed results. Non-null only when `status` is `"posted"`. |
| `thread_id` | `string \| null` | No | Channel-specific thread or message identifier (e.g., Slack timestamp string). `null` for copy-only stubs and failed results. |
| `status` | `string` enum | Yes | One of `"copy-only"`, `"posted"`, `"failed"`. See status axis below. |

---

## Status axis

| `status` | `posted` | `url` | `thread_id` | Meaning |
|---|---|---|---|---|
| `copy-only` | `false` | `null` | `null` | Stub produced text for manual paste. No network write occurred. |
| `posted` | `true` | non-null string | non-null string | Message was delivered. Permalink and thread id are populated. |
| `failed` | `false` | `null` | `null` | Adapter attempted delivery but encountered an error. |

These invariants are enforced by the schema's `if/then/else` conditionals. A document that violates them (e.g., `status: "copy-only"` with `posted: true`, or `status: "posted"` with `url: null`) will fail schema validation.

---

## Copy-only stub behavior

During the initial egress architecture phase, all channel adapters are copy-only stubs. A stub:

1. Composes the outbound message text.
2. Returns an `EgressResult` with `status: "copy-only"`, `posted: false`, `url: null`, and `thread_id: null`.
3. Does **not** make any network call or write to the channel.

The composed text is surfaced by the calling skill for manual paste by the operator. This is intentional: write-back is out of scope until the egress architecture is validated end-to-end.

Stubs must always return a schema-valid result. A stub that omits `status` or sets `posted: true` is a contract violation.

---

## Live adapter shape

When a channel adapter implements write-back (future work):

1. It posts the message to the channel.
2. On success, it returns `status: "posted"`, `posted: true`, a non-null `url` permalink, and a non-null `thread_id`.
3. On failure, it returns `status: "failed"`, `posted: false`, `url: null`, and `thread_id: null`. It must not throw; errors are surfaced via the status field.

The `url` format depends on the channel:
- **Slack:** `https://slack.com/archives/<channel_id>/<message_ts>` (with dot replaced by nothing in the path segment)
- Other channels: any stable permalink string

The `thread_id` format depends on the channel:
- **Slack:** the message timestamp string (e.g., `"1234567890.123456"`)
- Other channels: a channel-native identifier

---

## Emitters

- **Copy-only stubs:** All current channel adapters under `agents/egress/`. Emit `status: "copy-only"`.
- **Live adapters (future):** Will emit `status: "posted"` on success, `status: "failed"` on error.

No other agent type emits this contract.

---

## Schema versioning

`schema_version` uses hard-cutover semantics. A bump from `"1"` to `"2"` is a breaking change. Consumers that receive an unknown `schema_version` must reject the document rather than attempt to parse it under the old schema. All adapters must be updated atomically when the version bumps.
