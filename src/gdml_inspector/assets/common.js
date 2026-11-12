// Small helpers shared by both viewers (injected at build time, like
// clipping.js): schema compatibility checking and chunk digest verification.

const SUPPORTED_SCHEMA_VERSION = 2;

// Schema 1 is everything exported before input/chunk digests were recorded
// (it carries no schemaVersion at all). Anything newer than this viewer reads
// is refused with guidance rather than half-working.
function schemaStatus(declared) {
  const version = declared === undefined ? 1 : declared;
  if (!Number.isInteger(version) || version < 1 || version > SUPPORTED_SCHEMA_VERSION) {
    return {
      level: 'future',
      message: `This file declares schema ${JSON.stringify(declared)}, which this viewer (schema ${SUPPORTED_SCHEMA_VERSION}) cannot read. It was probably exported by a newer gdml-inspector: update gdml-inspector, or re-export it with this version.`,
    };
  }
  if (version < SUPPORTED_SCHEMA_VERSION) {
    return {
      level: 'old',
      message: `This file uses schema ${version} (exported before input and chunk digests were recorded). It still displays, but its provenance cannot be verified: re-export it with a current gdml-inspector.`,
    };
  }
  return {level: 'ok', message: ''};
}

// Shows a non-ok status in the #compat element; returns the status.
function showSchemaStatus(status) {
  const box = document.getElementById('compat');
  if (box && status.level !== 'ok') {
    box.textContent = status.message;
    box.style.display = '';
  }
  return status;
}

// Lower-case hex SHA-256 of an ArrayBuffer, or null when the browser offers
// no SubtleCrypto (it requires a secure context: https or localhost).
async function sha256Hex(buffer) {
  if (!(window.crypto && window.crypto.subtle)) return null;
  const digest = await window.crypto.subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}
