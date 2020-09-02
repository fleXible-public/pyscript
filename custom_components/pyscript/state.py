"""Handles state variable access and change notification."""

import logging
from typing import Dict, Optional

from homeassistant.core import valid_entity_id, split_entity_id
from homeassistant.helpers.template import AllStates
from .const import LOGGER_PATH

_LOGGER = logging.getLogger(LOGGER_PATH + ".state")


class State:
    """Class for state functions."""

    def __init__(self, hass, handler_func):
        """Initialize State."""

        self.hass = hass
        self.handler = handler_func
        #
        # notify message queues by variable
        #
        self.notify = {}

        #
        # Last value of state variable notifications.  We maintain this
        # so that trigger evaluation can use the last notified value,
        # rather than fetching the current value, which is subject to
        # race conditions multiple state variables are set.
        #
        self.notify_var_last = {}

        #
        # `states` object from template extensions, working exactly the
        # same way as documented.
        # Attribute "_is_coroutine" is needed for "asyncio.iscoroutinefunction()"
        # fails in eval.
        self._states = AllStates(hass)
        setattr(self._states, "_is_coroutine", None)

    def notify_add(self, var_names, queue):
        """Register to notify state variables changes to be sent to queue."""

        for var_name in var_names if isinstance(var_names, list) else [var_names]:
            parts = var_name.split(".")
            if len(parts) != 2 and len(parts) != 3:
                continue
            state_var_name = f"{parts[0]}.{parts[1]}"
            if state_var_name not in self.notify:
                self.notify[state_var_name] = {}
            self.notify[state_var_name][queue] = var_names

    def notify_del(self, var_names, queue):
        """Unregister notify of state variables changes for given queue."""

        for var_name in var_names if isinstance(var_names, list) else [var_names]:
            parts = var_name.split(".")
            if len(parts) != 2 and len(parts) != 3:
                continue
            state_var_name = f"{parts[0]}.{parts[1]}"
            if (
                state_var_name not in self.notify
                or queue not in self.notify[state_var_name]
            ):
                return
            del self.notify[state_var_name][queue]

    async def update(self, new_vars, func_args):
        """Deliver all notifications for state variable changes."""

        notify = {}
        for var_name, var_val in new_vars.items():
            if var_name in self.notify:
                self.notify_var_last[var_name] = var_val
                notify.update(self.notify[var_name])

        if notify:
            _LOGGER.debug("state.update(%s, %s)", new_vars, func_args)
            for queue, var_names in notify.items():
                await queue.put(["state", [self.notify_var_get(var_names), func_args]])

    def notify_var_get(self, var_names):
        """Return the most recent value of a state variable change."""
        new_vars = {}
        for var_name in var_names if var_names is not None else []:
            if var_name in self.notify_var_last:
                new_vars[var_name] = self.notify_var_last[var_name]
        return new_vars

    def set(self, var_name, value, attributes=None, **kwargs):
        """Set a state variable and optional attributes in hass."""
        if len(var_name.split(".")) != 2:
            _LOGGER.error(
                "invalid variable name %s (should be 'domain.entity')", var_name
            )
            return
        if attributes or kwargs:
            if attributes is None:
                attributes = {}
            attributes.update(kwargs)
        _LOGGER.debug("setting %s = %s, attr = %s", var_name, value, attributes)
        self.hass.states.async_set(var_name, value, attributes)

    def set_new(
        self,
        entity_id: str,
        new_state: str,
        attributes: Optional[Dict] = None,
    ) -> None:
        """Set the state of an entity, add entity if it does not exist.

        Attributes is an optional dict to specify attributes of this state.
        To remove existing attributes, set to empty dict.
        Default is to preserve them."""
        if not valid_entity_id(entity_id):
            _LOGGER.error(
                "invalid entity_id %s (should be 'domain.entity')", entity_id
            )
            return
        if attributes == {}:
            _LOGGER.debug("setting %s = %s, attr = %s", entity_id, new_state, None)
            self.hass.states.async_set(entity_id, new_state)
        else:
            old_state = self.hass.states.get(entity_id)
            old_attrs = getattr(old_state, "attributes", {})
            updated_attrs = dict(old_attrs)
            if attributes:
                updated_attrs.update(attributes)
            _LOGGER.debug("setting %s = %s, attr = %s", entity_id, new_state, updated_attrs)
            self.hass.states.async_set(entity_id, new_state, updated_attrs)

    def exist(self, var_name):
        """Check if a state variable value or attribute exists in hass."""
        parts = var_name.split(".")
        if len(parts) != 2 and len(parts) != 3:
            return False
        value = self.hass.states.get(f"{parts[0]}.{parts[1]}")
        return value and (len(parts) == 2 or value.attributes.get(parts[2]) is not None)

    def get(self, var_name):
        """Get a state variable value or attribute from hass."""
        entity_id, attr_name = var_name, None
        num_period = var_name.count(".")
        if num_period == 2:
            entity_id, attr_name = var_name.rsplit(".", maxsplit=1)
        if num_period > 2 or valid_entity_id(entity_id) is False:
            return None

        state = self.hass.states.get(entity_id)
        if state and attr_name:
            return state.attributes.get(attr_name)
        else:
            return getattr(state, "state", None)

    def get_new(self, entity_id):
        """Retrieve state of entity_id or None if not found. """
        return self.hass.states.get(entity_id)

    def completions(self, root):
        """Return possible completions of state variables."""
        words = set()
        num_period = root.count(".")
        if num_period == 2:
            #
            # complete state attributes
            #
            last_period = root.rfind(".")
            name = root[0:last_period]
            value = self.hass.states.get(name)
            if value:
                attr_root = root[last_period + 1 :]
                for attr_name in value.attributes.keys():
                    if attr_name.lower().startswith(attr_root):
                        words.add(f"{name}.{attr_name}")
        elif num_period < 2:
            #
            # complete among all state names
            #
            for name in self.hass.states.async_all():
                if name.entity_id.lower().startswith(root):
                    words.add(name.entity_id)
        return words

    def register_functions(self):
        """Register state functions."""
        functions = {
            "state.get": self.get_new,
            "state.set": self.set_new,
            "states": self._states,
        }
        self.handler.register(functions)
