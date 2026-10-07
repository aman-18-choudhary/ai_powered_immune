"""Ground truth over generated campaigns (anything not listed is benign)."""

from datetime import datetime

from .scam import MASS_VICTIM_THRESHOLD, Campaign


class GroundTruth:
    def __init__(self, campaigns: list[Campaign]) -> None:
        self._campaigns = {c.campaign_id: c for c in campaigns}
        self._txn_campaign: dict[str, str] = {}
        self._txn_role: dict[str, str] = {}
        self._call_campaign: dict[str, str] = {}
        for c in campaigns:
            for t in c.txns:
                self._txn_campaign[t.txn_id] = c.campaign_id
                self._txn_role[t.txn_id] = c.txn_roles[t.txn_id]
            for e in c.calls:
                self._call_campaign[e.call_id] = c.campaign_id

    def is_scam_txn(self, txn_id: str) -> bool:
        return txn_id in self._txn_campaign

    def campaign_of(self, txn_id: str) -> str | None:
        return self._txn_campaign.get(txn_id)

    def txn_role(self, txn_id: str) -> str | None:
        """'victim_transfer' | 'mule_forward' | None (benign)."""
        return self._txn_role.get(txn_id)

    def is_scam_call(self, call_id: str) -> bool:
        return call_id in self._call_campaign

    def campaign_of_call(self, call_id: str) -> str | None:
        return self._call_campaign.get(call_id)

    def first_signal_ts(self, campaign_id: str) -> datetime:
        """Earliest observable event (call or transaction) of the campaign."""
        c = self._campaigns[campaign_id]
        return min([e.ts for e in c.calls] + [t.ts for t in c.txns])

    def mass_victimisation_ts(self, campaign_id: str) -> datetime:
        """Time the victim count crosses 10 (first transfer of the 10th victim)."""
        c = self._campaigns[campaign_id]
        if len(c.victim_first_txn_ts) < MASS_VICTIM_THRESHOLD:
            raise ValueError(
                f"campaign {campaign_id} has only {len(c.victim_first_txn_ts)} victims "
                f"(< {MASS_VICTIM_THRESHOLD})"
            )
        return sorted(c.victim_first_txn_ts)[MASS_VICTIM_THRESHOLD - 1]

    def has_mass_victimisation(self, campaign_id: str) -> bool:
        """True when the campaign reaches 10 victims at all. Task 8 must check this (or use
        ``mass_victimisation_ts_or_none``) instead of calling ``mass_victimisation_ts`` blind."""
        return len(self._campaigns[campaign_id].victim_first_txn_ts) >= MASS_VICTIM_THRESHOLD

    def mass_victimisation_ts_or_none(self, campaign_id: str) -> datetime | None:
        """``mass_victimisation_ts`` or None for campaigns that never reach 10 victims
        (the evaluation-safe accessor; ``mass_victimisation_ts`` raises ValueError)."""
        if not self.has_mass_victimisation(campaign_id):
            return None
        return self.mass_victimisation_ts(campaign_id)
