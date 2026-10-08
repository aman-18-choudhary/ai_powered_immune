"""Topic names and the keying contract.

Keying contract (ordering): Kafka only orders messages within a partition, and txn-guard's
features (history, velocity, call-risk window) depend on per-payer order. Therefore

* ``txn.events`` MUST be keyed by ``Transaction.payer_token``,
* ``call.events`` by ``CallEvent.victim_token`` and ``call.risk`` by ``CallRisk.victim_token``
  (the payer token and the victim token are the same pseudonymous identity domain: the
  tokenizer maps one citizen to one token),
* ``txn.decisions`` is keyed by ``txn_id``. Consumers of decisions must dedupe on
  ``(txn_id, decision_seq)`` and act on the highest ``decision_seq`` (upgrades are new events
  with a larger seq; a missing ``decision_seq`` in an old payload means 1).
"""


class Topics:
    TXN_EVENTS = "txn.events"
    CALL_EVENTS = "call.events"
    CALL_RISK = "call.risk"
    TXN_DECISIONS = "txn.decisions"
    ANTIBODIES = "antibody.published"
    LEDGER = "ledger.append"
    COMPLAINTS = "complaints"
    DLQ_SUFFIX = ".dlq"
