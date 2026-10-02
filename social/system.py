"""pairwise-social-system - SocialSystem

A social system with confirmed friendships, presence heartbeats,
expiring friend requests, intimacy-based interactions and
incremental, conflict-aware, backed-up persistence.
"""

import copy
import time


class SocialSystem:
    PRESENCE_TYPES = ("online", "away", "busy", "invisible", "offline")
    PRIVACY_LEVELS = ("public", "friends", "private")

    HEARTBEAT_TIMEOUT = 30.0
    REQUEST_TTL = 7 * 24 * 3600.0
    ADD_COOLDOWN = 5.0
    DAILY_INTERACT_LIMIT = 20
    INTIMACY_PER_INTERACTION = 5
    MAX_BACKUPS = 3
    REWARD_TIERS = ((100, "gold_gift"), (50, "silver_gift"), (10, "bronze_gift"))

    def __init__(self, clock=None):
        self._clock = clock or time.time
        self.state = {}
        self._friends = {}
        self._blacklist = {}
        self._pending_requests = {}
        self._pending_confirmations = {}
        self._last_add_attempt = {}
        self._notifications = {}
        self._presence = {}
        self._intimacy = {}
        self._daily_interactions = {}
        self._rewards = {}
        self._versions = {}
        self._dirty = set()
        self._snapshot = {}
        self.backups = []

    # ------------------------------------------------------------------ utils
    def _now(self, now):
        return float(now) if now is not None else float(self._clock())

    def _mark_dirty(self, *keys):
        self._dirty.update(keys)
        for key in keys:
            self._versions[key] = self._versions.get(key, 0) + 1

    @staticmethod
    def _pair(a, b):
        return tuple(sorted((a, b)))

    def _day(self, now):
        return int(now // 86400)

    # ------------------------------------------------------------- friendship
    def block_user(self, user, other):
        self._blacklist.setdefault(user, set()).add(other)

    def _blocked(self, a, b):
        return b in self._blacklist.get(a, set()) or a in self._blacklist.get(b, set())

    def add_friend(self, user=None, friend=None, now=None):
        """Mutual-confirmation friend add with blacklist check and cooldown."""
        if user is None or friend is None:
            return {"ok": False, "reason": "missing_users"}
        if user == friend:
            return {"ok": False, "reason": "self_friend"}
        ts = self._now(now)
        if self._blocked(user, friend):
            return {"ok": False, "reason": "blacklisted"}
        pair = self._pair(user, friend)
        last = self._last_add_attempt.get(pair)
        if last is not None and ts - last < self.ADD_COOLDOWN:
            return {"ok": False, "reason": "cooldown",
                    "retry_after": self.ADD_COOLDOWN - (ts - last)}
        self._last_add_attempt[pair] = ts
        if friend in self._friends.get(user, set()):
            return {"ok": False, "reason": "already_friends"}

        # Mutual confirmation: only the second side confirming establishes the bond.
        if user in self._pending_confirmations.get(friend, set()):
            self._pending_confirmations[friend].discard(user)
            self._friends.setdefault(user, set()).add(friend)
            self._friends.setdefault(friend, set()).add(user)
            self._mark_dirty("friends")
            return {"ok": True, "confirmed": True, "user": user, "friend": friend}
        self._pending_confirmations.setdefault(user, set()).add(friend)
        self._notify(friend, "friend_confirm", {"from": user})
        return {"ok": True, "confirmed": False, "awaiting": friend}

    # --------------------------------------------------------------- presence
    def update_presence(self, user=None, status="online", privacy=None, now=None):
        """Presence with heartbeat, typed status and privacy settings."""
        if user is None:
            return {"ok": False, "reason": "missing_user"}
        if status not in self.PRESENCE_TYPES:
            return {"ok": False, "reason": "invalid_status",
                    "allowed": list(self.PRESENCE_TYPES)}
        ts = self._now(now)
        record = self._presence.setdefault(
            user, {"status": "offline", "privacy": "public", "heartbeat": 0.0})
        record["status"] = status
        record["heartbeat"] = ts
        if privacy is not None:
            if privacy not in self.PRIVACY_LEVELS:
                return {"ok": False, "reason": "invalid_privacy",
                        "allowed": list(self.PRIVACY_LEVELS)}
            record["privacy"] = privacy
        self._mark_dirty("presence")
        return {"ok": True, "user": user, "status": status,
                "privacy": record["privacy"], "heartbeat": ts}

    def get_presence(self, user, viewer=None, now=None):
        record = self._presence.get(user)
        if record is None:
            return "offline"
        ts = self._now(now)
        if record["status"] != "offline" and (
                ts - record["heartbeat"] > self.HEARTBEAT_TIMEOUT):
            record["status"] = "offline"
        privacy = record["privacy"]
        if viewer is not None and viewer != user:
            if privacy == "private" or (
                    privacy == "friends"
                    and viewer not in self._friends.get(user, set())):
                return "offline"
        if record["status"] == "invisible" and viewer is not None and viewer != user:
            return "offline"
        return record["status"]

    # -------------------------------------------------------- friend requests
    def send_friend_request(self, sender=None, receiver=None, now=None):
        """Friend requests with duplicate check, expiry and notification."""
        if sender is None or receiver is None:
            return {"ok": False, "reason": "missing_users"}
        ts = self._now(now)
        if self._blocked(sender, receiver):
            return {"ok": False, "reason": "blacklisted"}
        if receiver in self._friends.get(sender, set()):
            return {"ok": False, "reason": "already_friends"}
        inbox = self._pending_requests.setdefault(receiver, {})
        self._expire_requests(receiver, ts)
        if sender in inbox:
            return {"ok": False, "reason": "duplicate",
                    "expires_in": self.REQUEST_TTL - (ts - inbox[sender])}
        inbox[sender] = ts
        self._notify(receiver, "friend_request", {"from": sender})
        self._mark_dirty("requests")
        return {"ok": True, "from": sender, "to": receiver,
                "expires_at": ts + self.REQUEST_TTL}

    def _expire_requests(self, receiver, now):
        inbox = self._pending_requests.get(receiver, {})
        expired = [s for s, t in inbox.items() if now - t > self.REQUEST_TTL]
        for sender in expired:
            del inbox[sender]

    def accept_friend_request(self, receiver, sender, now=None):
        ts = self._now(now)
        inbox = self._pending_requests.get(receiver, {})
        self._expire_requests(receiver, ts)
        if sender not in inbox:
            return {"ok": False, "reason": "no_active_request"}
        del inbox[sender]
        self._friends.setdefault(receiver, set()).add(sender)
        self._friends.setdefault(sender, set()).add(receiver)
        self._notify(sender, "request_accepted", {"by": receiver})
        self._mark_dirty("friends", "requests")
        return {"ok": True, "user": receiver, "friend": sender}

    def _notify(self, user, kind, payload):
        self._notifications.setdefault(user, []).append(
            {"type": kind, "data": payload, "read": False})

    def get_notifications(self, user):
        return list(self._notifications.get(user, []))

    # ------------------------------------------------------------ interaction
    def interact_with_friend(self, user=None, friend=None, kind="chat", now=None):
        """Interactions build intimacy, are daily-limited and grant rewards."""
        if user is None or friend is None:
            return {"ok": False, "reason": "missing_users"}
        if friend not in self._friends.get(user, set()):
            return {"ok": False, "reason": "not_friends"}
        ts = self._now(now)
        day_key = (self._pair(user, friend), self._day(ts))
        used = self._daily_interactions.get(day_key, 0)
        if used >= self.DAILY_INTERACT_LIMIT:
            return {"ok": False, "reason": "daily_limit",
                    "limit": self.DAILY_INTERACT_LIMIT}
        self._daily_interactions[day_key] = used + 1

        pair = self._pair(user, friend)
        before = self._intimacy.get(pair, 0)
        after = before + self.INTIMACY_PER_INTERACTION
        self._intimacy[pair] = after

        reward = None
        for threshold, name in self.REWARD_TIERS:
            if before < threshold <= after:
                reward = name
                self._rewards.setdefault(user, []).append(
                    {"reward": name, "with": friend, "at": ts})
                break
        self._mark_dirty("intimacy")
        return {"ok": True, "intimacy": after, "level": self._intimacy_level(after),
                "remaining_today": self.DAILY_INTERACT_LIMIT - used - 1,
                "reward": reward}

    @staticmethod
    def _intimacy_level(score):
        if score >= 100:
            return "soulmate"
        if score >= 50:
            return "close"
        if score >= 10:
            return "familiar"
        return "stranger"

    # ------------------------------------------------------------ persistence
    def _serialize(self):
        return {
            "friends": {u: sorted(f) for u, f in self._friends.items()},
            "presence": copy.deepcopy(self._presence),
            "requests": {u: dict(i) for u, i in self._pending_requests.items()},
            "intimacy": {str(k): v for k, v in self._intimacy.items()},
        }

    def save_social_data(self, remote=None, now=None):
        """Incremental save with version-based conflict resolution and backups."""
        current = self._serialize()
        merged_from_remote = []
        if remote:
            data = remote.get("data", remote)
            versions = remote.get("versions", {})
            for key, value in data.items():
                if versions.get(key, 0) > self._versions.get(key, 0):
                    current[key] = copy.deepcopy(value)
                    self._versions[key] = versions[key]
                    merged_from_remote.append(key)
                    self._dirty.add(key)

        changed = sorted(k for k in self._dirty if k in current)
        if self._snapshot:
            self.backups.append(copy.deepcopy(self._snapshot))
            del self.backups[:-self.MAX_BACKUPS]
        for key in changed:
            self._snapshot[key] = copy.deepcopy(current[key])
        self._dirty.clear()
        return {"ok": True, "incremental": True, "saved": changed,
                "versions": dict(self._versions),
                "merged_from_remote": merged_from_remote,
                "backup_count": len(self.backups)}

    def restore_from_backup(self, index=-1):
        if not self.backups:
            return {"ok": False, "reason": "no_backups"}
        self._snapshot = copy.deepcopy(self.backups[index])
        self._dirty.update(self._snapshot)
        return {"ok": True, "restored": sorted(self._snapshot)}
