from hashlib import sha256

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db import models
from django.utils.encoding import force_bytes, force_text
from django.utils.functional import cached_property

from . import hkdf


__all__ = [
    'EncryptedField',
    'EncryptedTextField',
    'EncryptedCharField',
    'EncryptedEmailField',
    'EncryptedIntegerField',
    'EncryptedDateField',
    'EncryptedDateTimeField',
    'DualField',
    'DualTextField',
    'DualCharField',
    'DualEmailField',
    'DualIntegerField',
    'DualDateField',
    'DualDateTimeField',
]


class EncryptedField(models.Field):
    """A field that encrypts values using Fernet symmetric encryption."""
    def __init__(self, *args, **kwargs):
        if kwargs.get('primary_key'):
            raise ImproperlyConfigured(
                "EncryptedField does not support primary_key=True."
            )
        if kwargs.get('unique'):
            raise ImproperlyConfigured(
                "EncryptedField does not support unique=True."
            )
        if kwargs.get('db_index'):
            raise ImproperlyConfigured(
                "EncryptedField does not support db_index=True."
            )
        super(EncryptedField, self).__init__(*args, **kwargs)

    @cached_property
    def keys(self):
        keys = getattr(settings, 'FERNET_KEYS', None)
        if keys is None:
            keys = [settings.SECRET_KEY]
        return keys

    @cached_property
    def fernet_keys(self):
        if getattr(settings, 'FERNET_USE_HKDF', True):
            return [hkdf.derive_fernet_key(k) for k in self.keys]
        return self.keys

    @cached_property
    def fernet(self):
        if len(self.fernet_keys) == 1:
            return Fernet(self.fernet_keys[0])
        return MultiFernet([Fernet(k) for k in self.fernet_keys])

    def get_internal_type(self):
        return 'BinaryField'

    def get_db_prep_save(self, value, connection):
        value = super(
            EncryptedField, self
        ).get_db_prep_save(value, connection)
        if value is not None:
            retval = self.fernet.encrypt(force_bytes(value))
            return connection.Database.Binary(retval)

    def get_prep_lookup(self, lookup_type, value):
        raise FieldError(
            "Encrypted field '%s' does not support lookups." % self.name
        )

    def from_db_value(self, value, expression, connection, context):
        if value is not None:
            value = bytes(value)
            return self.to_python(force_text(self.fernet.decrypt(value)))


class EncryptedTextField(EncryptedField, models.TextField):
    pass


class EncryptedCharField(EncryptedField, models.CharField):
    pass


class EncryptedEmailField(EncryptedField, models.EmailField):
    pass


class EncryptedIntegerField(EncryptedField, models.IntegerField):
    pass


class EncryptedDateField(EncryptedField, models.DateField):
    pass


class EncryptedDateTimeField(EncryptedField, models.DateTimeField):
    pass


NO_VALUE = object()


class DualFieldDescriptor(object):
    def __init__(self, encrypted_field_attname):
        self.encrypted_field_attname = encrypted_field_attname

    def __get__(self, obj, cls=None):
        return getattr(obj, self.encrypted_field_attname)

    def __set__(self, obj, value):
        if value is not NO_VALUE:
            return setattr(obj, self.encrypted_field_attname, value)


class DualField(models.Field):
    encrypted_field_class = EncryptedField

    def __init__(self, *args, **kwargs):
        if kwargs.get('primary_key'):
            raise ImproperlyConfigured(
                "DualField does not support primary_key=True."
            )
        super(DualField, self).__init__(*args, **kwargs)
        self.populate_from_field = self.encrypted_field_class(
            editable=False, null=self.null)
        # Ensure that the encrypted field has a lower creation counter than any
        # other field, so Model.__init__() will try to populate it first (since
        # it will be populated with the default value initially, and only with
        # the real value when the main DualField's value is set).
        self.populate_from_field.creation_counter = -1

    def contribute_to_class(self, cls, name, *a, **kw):
        super(DualField, self).contribute_to_class(cls, name, *a, **kw)
        encrypted_field_name = name + '_encrypted'
        self.populate_from_field.contribute_to_class(cls, encrypted_field_name)
        descriptor = DualFieldDescriptor(self.populate_from_field.attname)
        setattr(cls, name, descriptor)

    def get_internal_type(self):
        return 'BinaryField'

    def _hash_value(self, val):
        return sha256(force_bytes(val)).digest()

    def get_db_prep_value(self, value, connection, *a, **kw):
        value = super(
            DualField, self
        ).get_db_prep_value(value, connection, *a, **kw)
        if value is not None:
            return connection.Database.Binary(self._hash_value(value))

    def pre_save(self, instance, add):
        return self.populate_from_field.value_from_object(instance)

    def get_prep_lookup(self, lookup_type, value):
        if lookup_type not in {'exact', 'in', 'isnull'}:
            raise FieldError(
                "DualField '%s' supports only exact, in, and isnull lookups."
                % self.name
            )
        return super(DualField, self).get_prep_lookup(lookup_type, value)

    def from_db_value(self, value, expression, connection, context):
        return NO_VALUE


class DualTextField(DualField, models.TextField):
    encrypted_field_class = EncryptedTextField


class DualCharField(DualField, models.CharField):
    encrypted_field_class = EncryptedCharField


class DualEmailField(DualField, models.EmailField):
    encrypted_field_class = EncryptedEmailField


class DualIntegerField(DualField, models.IntegerField):
    encrypted_field_class = EncryptedIntegerField


class DualDateField(DualField, models.DateField):
    encrypted_field_class = EncryptedDateField


class DualDateTimeField(DualField, models.DateTimeField):
    encrypted_field_class = EncryptedDateTimeField
