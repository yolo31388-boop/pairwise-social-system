"""
pairwise-social-system - SocialSystem

社交系统核心实现：
- 加好友：双向确认 + 黑名单检查 + 冷却
- 在线状态：心跳 + 状态类型 + 隐私设置
- 好友请求：重复检查 + 过期 + 通知
- 好友互动：亲密度 + 每日限制 + 奖励
- 社交持久化：增量保存 + 冲突解决 + 备份
"""

import time
from copy import deepcopy


class SocialSystem:
    REQUEST_TTL = 7 * 24 * 3600          # 好友请求有效期（秒）
    REQUEST_COOLDOWN = 5.0               # 发送好友请求冷却（秒）
    ADD_FRIEND_COOLDOWN = 5.0            # 加好友操作冷却（秒）
    HEARTBEAT_TIMEOUT = 30.0             # 心跳超时，超过视为离线（秒）
    DAILY_INTERACTION_LIMIT = 20         # 每日计入亲密度的互动上限
    MAX_BACKUPS = 5                      # 保留的备份数量

    VALID_STATUSES = ("online", "away", "busy", "invisible", "offline")
    VALID_PRIVACY = ("public", "friends", "private")

    INTIMACY_GAIN = {"chat": 2, "gift": 5, "game": 3, "visit": 1}
    INTIMACY_REWARDS = {10: "badge_acquaintance", 50: "badge_familiar", 100: "badge_bestie"}

    def __init__(self):
        self.state = {}
        self.friends = {}                     # user_id -> set(friend_id)
        self.blacklists = {}                  # user_id -> set(blocked_id)
        self.pending_requests = {}            # request_id -> request
        self.confirmations = {}               # user_id -> set(待确认对方 id)
        self.cooldowns = {}                   # (action, user_id) -> 上次时间戳
        self.presence = {}                    # user_id -> {status, privacy, last_heartbeat}
        self.intimacy = {}                    # (a, b) -> 亲密度
        self.daily_interactions = {}          # user_id -> {"date": str, "count": int}
        self.rewards = {}                     # user_id -> [reward]
        self.notifications = {}               # user_id -> [notification]
        self.saved_data = {}                  # 已持久化数据
        self.saved_versions = {}              # key -> 已保存版本号
        self.versions = {}                    # key -> 当前版本号
        self.dirty = set()                    # 待保存的 key（增量）
        self.backups = []                     # 历史备份
        self._request_seq = 0

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _now(self, now):
        return time.time() if now is None else now

    def _pair(self, a, b):
        return (a, b) if a <= b else (b, a)

    def _is_blocked(self, user_id, other_id):
        return other_id in self.blacklists.get(user_id, set())

    def _check_cooldown(self, action, user_id, now, cooldown):
        key = (action, user_id)
        last = self.cooldowns.get(key)
        if last is not None and now - last < cooldown:
            return False
        self.cooldowns[key] = now
        return True

    def _notify(self, user_id, kind, payload):
        self.notifications.setdefault(user_id, []).append(
            {"type": kind, "payload": payload, "time": self._now(payload.get("time"))}
        )

    def _mark_dirty(self, key):
        self.versions[key] = self.versions.get(key, 0) + 1
        self.dirty.add(key)

    def block_user(self, user_id, blocked_id):
        self.blacklists.setdefault(user_id, set()).add(blocked_id)
        self._mark_dirty("blacklists")

    # ------------------------------------------------------------------
    # 好友请求：重复检查 + 过期 + 通知
    # ------------------------------------------------------------------
    def _purge_expired_requests(self, now):
        expired = [
            rid for rid, req in self.pending_requests.items()
            if now >= req["expires_at"]
        ]
        for rid in expired:
            del self.pending_requests[rid]

    def send_friend_request(self, sender=None, recipient=None, now=None):
        now = self._now(now)
        self._purge_expired_requests(now)
        if sender is None or recipient is None:
            return {"success": False, "reason": "missing_user"}
        if sender == recipient:
            return {"success": False, "reason": "self_request"}
        if self._is_blocked(recipient, sender) or self._is_blocked(sender, recipient):
            return {"success": False, "reason": "blacklisted"}
        if recipient in self.friends.get(sender, set()):
            return {"success": False, "reason": "already_friends"}
        # 重复检查：同一对人已有未过期请求则拒绝
        for req in self.pending_requests.values():
            if req["sender"] == sender and req["recipient"] == recipient:
                return {"success": False, "reason": "duplicate_request"}
        # 冷却：短时间内不能疯狂发请求
        if not self._check_cooldown("friend_request", sender, now, self.REQUEST_COOLDOWN):
            return {"success": False, "reason": "cooldown"}

        self._request_seq += 1
        request_id = f"req_{self._request_seq}"
        self.pending_requests[request_id] = {
            "sender": sender,
            "recipient": recipient,
            "created_at": now,
            "expires_at": now + self.REQUEST_TTL,   # 过期时间
        }
        # 通知被加的人
        self._notify(recipient, "friend_request",
                     {"request_id": request_id, "from": sender, "time": now})
        self._mark_dirty("pending_requests")
        return {"success": True, "request_id": request_id,
                "expires_at": now + self.REQUEST_TTL}

    # ------------------------------------------------------------------
    # 加好友：双向确认 + 黑名单检查 + 冷却
    # ------------------------------------------------------------------
    def add_friend(self, user_a=None, user_b=None, now=None):
        now = self._now(now)
        self._purge_expired_requests(now)
        if user_a is None or user_b is None:
            return {"success": False, "reason": "missing_user"}
        if user_a == user_b:
            return {"success": False, "reason": "self_friend"}
        # 黑名单检查：任一方拉黑即拒绝
        if self._is_blocked(user_a, user_b) or self._is_blocked(user_b, user_a):
            return {"success": False, "reason": "blacklisted"}
        if user_b in self.friends.get(user_a, set()):
            return {"success": True, "status": "already_friends"}
        # 冷却：限制短时间内频繁加好友
        if not self._check_cooldown("add_friend", user_a, now, self.ADD_FRIEND_COOLDOWN):
            return {"success": False, "reason": "cooldown"}

        # 双向确认：对方已确认过，才正式建立双向好友关系
        confirmed_by_other = user_a in self.confirmations.get(user_b, set())
        if not confirmed_by_other:
            self.confirmations.setdefault(user_a, set()).add(user_b)
            self._notify(user_b, "friend_confirm",
                         {"from": user_a, "time": now})
            return {"success": True, "status": "pending_confirmation"}

        self.confirmations[user_b].discard(user_a)
        self.friends.setdefault(user_a, set()).add(user_b)
        self.friends.setdefault(user_b, set()).add(user_a)
        self._notify(user_a, "friend_added", {"friend": user_b, "time": now})
        self._notify(user_b, "friend_added", {"friend": user_a, "time": now})
        self._mark_dirty("friends")
        return {"success": True, "status": "friends"}

    # ------------------------------------------------------------------
    # 在线状态：心跳 + 状态类型 + 隐私设置
    # ------------------------------------------------------------------
    def update_presence(self, user_id=None, status="online", privacy=None, now=None):
        now = self._now(now)
        if user_id is None:
            return {"success": False, "reason": "missing_user"}
        if status not in self.VALID_STATUSES:
            return {"success": False, "reason": "invalid_status"}
        record = self.presence.setdefault(user_id, {
            "status": "offline", "privacy": "public", "last_heartbeat": 0.0,
        })
        record["status"] = status
        record["last_heartbeat"] = now          # 心跳：每次更新刷新心跳
        if privacy is not None:
            if privacy not in self.VALID_PRIVACY:
                return {"success": False, "reason": "invalid_privacy"}
            record["privacy"] = privacy
        self._mark_dirty("presence")
        return {"success": True, "presence": dict(record)}

    def heartbeat(self, user_id, now=None):
        now = self._now(now)
        record = self.presence.get(user_id)
        if record is None:
            return {"success": False, "reason": "unknown_user"}
        record["last_heartbeat"] = now
        self._mark_dirty("presence")
        return {"success": True, "last_heartbeat": now}

    def get_presence(self, viewer_id, target_id, now=None):
        now = self._now(now)
        record = self.presence.get(target_id)
        if record is None:
            return {"status": "offline", "reason": "unknown_user"}
        # 心跳超时一律视为离线
        effective = record["status"]
        if now - record["last_heartbeat"] > self.HEARTBEAT_TIMEOUT:
            effective = "offline"
        if effective == "invisible" and viewer_id != target_id:
            effective = "offline"
        # 隐私设置：控制谁能看到真实状态
        privacy = record["privacy"]
        if viewer_id != target_id:
            if privacy == "private":
                effective = "offline"
            elif privacy == "friends" and viewer_id not in self.friends.get(target_id, set()):
                effective = "offline"
        return {"status": effective, "privacy": privacy}

    # ------------------------------------------------------------------
    # 好友互动：亲密度 + 每日限制 + 奖励
    # ------------------------------------------------------------------
    def interact_with_friend(self, user_id=None, friend_id=None, kind="chat", now=None):
        now = self._now(now)
        if user_id is None or friend_id is None:
            return {"success": False, "reason": "missing_user"}
        if friend_id not in self.friends.get(user_id, set()):
            return {"success": False, "reason": "not_friends"}

        # 每日限制：超过上限的互动不再涨亲密度
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        daily = self.daily_interactions.setdefault(user_id, {"date": today, "count": 0})
        if daily["date"] != today:
            daily["date"] = today
            daily["count"] = 0
        if daily["count"] >= self.DAILY_INTERACTION_LIMIT:
            pair = self._pair(user_id, friend_id)
            return {"success": False, "reason": "daily_limit",
                    "intimacy": self.intimacy.get(pair, 0)}
        daily["count"] += 1

        # 亲密度：不同互动类型涨不同点数
        pair = self._pair(user_id, friend_id)
        gain = self.INTIMACY_GAIN.get(kind, 1)
        self.intimacy[pair] = self.intimacy.get(pair, 0) + gain
        score = self.intimacy[pair]

        # 奖励：亲密度达到里程碑发放奖励
        reward = None
        earned = self.rewards.setdefault(user_id, [])
        for threshold, name in sorted(self.INTIMACY_REWARDS.items()):
            if score >= threshold and name not in earned and score - gain < threshold:
                reward = name
        if reward:
            earned.append(reward)
            self.rewards.setdefault(friend_id, []).append(reward)
            self._notify(user_id, "reward", {"reward": reward, "time": now})
            self._notify(friend_id, "reward", {"reward": reward, "time": now})

        self._mark_dirty("intimacy")
        return {"success": True, "intimacy": score, "gain": gain,
                "daily_count": daily["count"], "reward": reward}

    # ------------------------------------------------------------------
    # 社交持久化：增量 + 冲突解决 + 备份
    # ------------------------------------------------------------------
    def _snapshot(self):
        return {
            "friends": {k: sorted(v) for k, v in self.friends.items()},
            "blacklists": {k: sorted(v) for k, v in self.blacklists.items()},
            "presence": deepcopy(self.presence),
            "intimacy": {f"{a}|{b}": v for (a, b), v in self.intimacy.items()},
            "rewards": deepcopy(self.rewards),
            "pending_requests": deepcopy(self.pending_requests),
        }

    def save_social_data(self, remote_data=None, remote_versions=None,
                         incremental=True, backup=True, resolver=None):
        # 冲突解决：远端数据与本地冲突时按版本号 last-write-wins，或用自定义 resolver
        conflicts = []
        if remote_data:
            remote_versions = remote_versions or {}
            for key, value in remote_data.items():
                local_ver = self.versions.get(key, 0)
                remote_ver = remote_versions.get(key, 0)
                if remote_ver > local_ver:
                    self.state[key] = value
                    self.versions[key] = remote_ver
                    self.dirty.add(key)
                elif remote_ver < local_ver:
                    conflicts.append(key)
                else:
                    if self.state.get(key) != value:
                        resolved = resolver(key, self.state.get(key), value) if resolver else value
                        self.state[key] = resolved
                        self.dirty.add(key)
                        conflicts.append(key)

        snapshot = self._snapshot()
        for key, value in snapshot.items():
            self.state[key] = value

        # 备份：覆盖前先备份上一份持久化数据
        backup_id = None
        if backup and self.saved_data:
            self.backups.append(deepcopy(self.saved_data))
            del self.backups[:-self.MAX_BACKUPS]
            backup_id = len(self.backups) - 1

        # 增量保存：只写脏 key；否则全量
        if incremental:
            keys_to_save = [k for k in snapshot if k in self.dirty or k not in self.saved_data]
            if remote_data:
                keys_to_save.extend(k for k in remote_data if k in self.dirty)
        else:
            keys_to_save = list(self.state.keys())

        saved_keys = []
        for key in dict.fromkeys(keys_to_save):
            if key in self.state:
                self.saved_data[key] = deepcopy(self.state[key])
                self.saved_versions[key] = self.versions.get(key, 0)
                saved_keys.append(key)
        self.dirty.clear()

        return {"success": True, "incremental": incremental,
                "saved_keys": saved_keys, "conflicts": conflicts,
                "backup_id": backup_id}

    def restore_from_backup(self, backup_id=-1):
        if not self.backups:
            return {"success": False, "reason": "no_backup"}
        self.saved_data = deepcopy(self.backups[backup_id])
        return {"success": True, "restored_keys": list(self.saved_data.keys())}
