from core.models import Notification
from core.serializers.restricted_model_serializer import FieldRestrictedSerializer


class NotificationSerializer(FieldRestrictedSerializer):

    class Meta:
        model = Notification
        fields = ['user', 'subject', 'message']
        read_only_fields = ['status_date', 'created_by', 'created_at']

    def to_internal_value(self, data):
        # `from core.schema import UserType` raised ImportError: the package
        # root exports no type names. `GlobalIDUtils.get_pk_flexible` is
        # what the rest of the codebase resolves ids with, and it also
        # accepts a bare pk, which `UserType.get_pk` did not.
        from core.schema.common import GlobalIDUtils

        data['user'] = GlobalIDUtils.get_pk_flexible(data.get('user'), expected_type='UserType')
        return super().to_internal_value(data)

    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['created_by'] = request.user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get('request')
        validated_data['created_by'] = request.user
        return super().update(instance, validated_data)
