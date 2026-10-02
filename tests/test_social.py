import pytest
from social.system import SocialSystem


class TestSocialSystem:

    def test_add_friend(self):
        """加好友不做双向确认，一方加了另一方就有"""
        obj = SocialSystem()
        result = obj.add_friend()
        self.assertIsNotNone(result)

    def test_update_presence(self):
        """在线状态不做心跳，下线了还显示在线"""
        obj = SocialSystem()
        result = obj.update_presence()
        self.assertIsNotNone(result)

    def test_send_friend_request(self):
        """好友请求不做重复检查，同一个人发多次"""
        obj = SocialSystem()
        result = obj.send_friend_request()
        self.assertIsNotNone(result)

    def test_interact_with_friend(self):
        """好友互动不做亲密度，互动多少次都一样"""
        obj = SocialSystem()
        result = obj.interact_with_friend()
        self.assertIsNotNone(result)

    def test_save_social_data(self):
        """社交持久化不做增量，全量保存慢"""
        obj = SocialSystem()
        result = obj.save_social_data()
        self.assertIsNotNone(result)

