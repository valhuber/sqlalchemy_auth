#!/usr/bin/python
"""
sqlalchemy_auth provides authorization mechanisms for SQLAlchemy DB access,
via 2 mechanisms.

1. All model classes can add implicit filters
2. All full object results can selectively block attribute access

Both these mechanisms rely on an "_effective_user" parameter, set in the 
AuthSession and automatically propagated to AuthQuery via "query". This
_effective_user is passed to add_auth_filters, blocked_read_attributes
and blocked_write_attributes.

_effective_user can be any type. If _effective_user is set to None, all
authorization is bypassed.

class_ and query_cls should both be passed to sessionmaker:

    sessionmaker(bind=engine, class_=AuthSession, query_cls=AuthQuery)

Your classes can implement add_auth_filters and _blocked_read/write_attributes,
overriding the defaults set in AuthBase.

See sqlalchemy_auth_test.py for full examples.
"""

import sqlalchemy
import sqlalchemy.orm
import sqlalchemy.orm.attributes


class AuthException(Exception):
    pass


class AuthSession(sqlalchemy.orm.session.Session):
    """
    AuthSession constructs all queries with the effective_user.
    """
    def __init__(self, effective_user=None, *args, **kwargs):
        self._effective_user = effective_user
        super().__init__(*args, **kwargs)

    def query(self, *args, **kwargs):
        # allow AuthQuery to know which user is doing the lookup
        return super().query(*args, effective_user=self._effective_user, **kwargs)


class AuthQuery(sqlalchemy.orm.query.Query):
    """
    AuthQuery provides a mechanism for returned objects to know which user looked them up.
    """
    def __init__(self, *args, effective_user=None, **kwargs):
        self._effective_user = effective_user
        super().__init__(*args, **kwargs)

    def _compile_context(self, labels=True):
        """When the statement is compiled, run add_auth_filters."""
        # WARNING: This is in the display path (via __str__); if you are debugging
        #  with pycharm and hit a breakpoint, this code will silently execute,
        #  potentially causing filters to be added twice. This should have no affect
        #  on the results.
        if self._effective_user is not None:
            entities = {}
            # find/eliminate duplicates
            for col in self.column_descriptions:
                entities[col['entity']] = True
            # add_auth_filters
            for entity in entities:
                if isinstance(entity, sqlalchemy.ext.declarative.api.DeclarativeMeta):
                    self = col['entity'].add_auth_filters(self, self._effective_user)

        return super()._compile_context(labels)

    def _execute_and_instances(self, querycontext):
        instances_generator = super()._execute_and_instances(querycontext)
        for row in instances_generator:
            # all queries come through here - including ones that don't return model instances
            #  (count, for example).
            # Assuming it's an uncommon occurrence, we'll try/accept (test this later)
            try:
                row._effective_user = self._effective_user
            except AttributeError:
                pass
            yield row


class _AuthBase:
    # make _effective_user exist at all times.
    #  This matters because sqlalchemy does some magic before __init__ is called.
    # We set it to simplify the logic in __getattribute__
    _effective_user = None
    _checking_authorization = False

    def get_blocked_read_attributes(self):
        if self._effective_user is not None:
            return self._blocked_read_attributes(self._effective_user)
        return []

    def get_blocked_write_attributes(self):
        if self._effective_user is not None:
            return self._blocked_write_attributes(self._effective_user)
        return []

    def get_read_attributes(self):
        attrs = [v for v in vars(self) if not v.startswith("_")]
        return set(attrs) - set(self.get_blocked_read_attributes())

    def get_write_attributes(self):
        attrs = [v for v in vars(self) if not v.startswith("_")]
        return set(attrs) - set(self.get_blocked_write_attributes())

    def __getattribute__(self, name):
        # __getattribute__ is called before __init__ by a SQLAlchemy decorator.

        # bypass our check if we're recursive
        # this allows _blocked_read_attributes to use self.*
        if super().__getattribute__("_checking_authorization") == True:
            return super().__getattribute__(name)

        # look up blocked attributes
        super().__setattr__("_checking_authorization", True)
        blocked = self.get_blocked_read_attributes()
        super().__setattr__("_checking_authorization", False)

        # take action
        if name in blocked:
            raise AuthException('{} may not access {} on {}'.format(self._effective_user, name, self.__class__))
        return super().__getattribute__(name)

    def __setattr__(self, name, value):
        if name in self.get_blocked_write_attributes():
            raise AuthException('{} may not access {} on {}'.format(self._effective_user, name, self.__class__))
        return super().__setattr__(name, value)


class AuthBase(_AuthBase):
    """
    Provide authorization behavior (default: allow everything).
    To block access, return blocked attributes in your own 
    _blocked_read_attributes or _blocked_write_attributes.

    Subclass using mixins or by passing the class into declarative_base:

        class Foo(Base, AuthBase):

    or 

        Base = declarative_base(cls=sqlalchemy_auth.AuthBase)    
    """

    @staticmethod
    def add_auth_filters(query, effective_user):
        """
        Override this to add implicit filters to a query, before any additional
        filters are added.
        """
        return query

    def _blocked_read_attributes(self, effective_user):
        """
        Override this method to block read access to attributes, but use 
        the get_ methods for access.

        Only called if effective_user != None.
        """
        return []

    def _blocked_write_attributes(self, effective_user):
        """
        Override this method to block write access to attributes, but use 
        the get_ methods for access.

        Only called if effective_user != None.
        """
        return []



