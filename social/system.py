"""
pairwise-social-system - SocialSystem

This module contains a deliberately broken implementation.
Fix all bugs so that tests pass.
"""


class SocialSystem:
    def __init__(self):
        self.state = {}

    def add_friend(self, *args, **kwargs):
        """BUG: 加好友不做双向确认，一方加了另一方就有"""
        return None

    def update_presence(self, *args, **kwargs):
        """BUG: 在线状态不做心跳，下线了还显示在线"""
        return None

    def send_friend_request(self, *args, **kwargs):
        """BUG: 好友请求不做重复检查，同一个人发多次"""
        return None

    def interact_with_friend(self, *args, **kwargs):
        """BUG: 好友互动不做亲密度，互动多少次都一样"""
        return None

    def save_social_data(self, *args, **kwargs):
        """BUG: 社交持久化不做增量，全量保存慢"""
        return None

