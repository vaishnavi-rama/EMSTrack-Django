import logging
from enum import Enum

from django.contrib.gis.db import models
from django.utils import timezone
from django.urls import reverse
from django.template.defaulttags import register
from django.utils.translation import ugettext_lazy as _

from emstrack.latlon import calculate_orientation, calculate_distance, stationary_radius
from emstrack.models import AddressModel, UpdatedByModel, defaults, UpdatedByHistoryModel
from emstrack.util import make_choices
from equipment.models import EquipmentHolder

logger = logging.getLogger(__name__)


# filters

@register.filter
def get_ambulance_status(key):
    return AmbulanceStatus[key].value


@register.filter
def get_ambulance_capability(key):
    return AmbulanceCapability[key].value


@register.filter
def get_location_type(key):
    return LocationType[key].value


@register.filter
def get_location_coordinates(key):
    return str(key.x) + ", " + str(key.y)


@register.filter
def get_call_status(key):
    return CallStatus[key].value


@register.filter
def get_call_priority(key):
    return CallPriority[key].value


@register.filter
def get_ambulance_call_status(key):
    return AmbulanceCallStatus[key].value


@register.filter
def get_waypoint_status(key):
    return WaypointStatus[key].value

# Ambulance location models


# Ambulance model

class AmbulanceStatus(Enum):
    UK = _('Unknown')
    AV = _('Available')
    OS = _('Out of service')
    PB = _('Incident bound')
    AP = _('At incident')
    HB = _('Hospital bound')
    AH = _('At hospital')
    BB = _('Base bound')
    AB = _('At base')
    WB = _('Waypoint bound')
    AW = _('At waypoint')


AmbulanceStatusOrder = [ 
    AmbulanceStatus.AV,
    AmbulanceStatus.PB,
    AmbulanceStatus.AP,
    AmbulanceStatus.HB,
    AmbulanceStatus.AH,
    AmbulanceStatus.BB,
    AmbulanceStatus.AB,
    AmbulanceStatus.WB,
    AmbulanceStatus.AW,
    AmbulanceStatus.OS,
    AmbulanceStatus.UK
] 


class AmbulanceCapability(Enum):
    B = _('Basic')
    A = _('Advanced')
    R = _('Rescue')
    M = _('Medic')


AmbulanceCapabilityOrder = [ 
    AmbulanceCapability.B,
    AmbulanceCapability.A,
    AmbulanceCapability.R,
    AmbulanceCapability.M
] 


class Ambulance(UpdatedByModel):

    # TODO: Should we consider denormalizing Ambulance to avoid duplication with AmbulanceUpdate?

    equipmentholder = models.OneToOneField(EquipmentHolder,
                                           on_delete=models.CASCADE,
                                           verbose_name=_('equipmentholder'))

    # ambulance properties
    identifier = models.CharField(_('identifier'), max_length=50, unique=True)

    # TODO: Should we add an active flag?

    capability = models.CharField(_('capability'), max_length=1,
                                  choices=make_choices(AmbulanceCapability))

    # status
    status = models.CharField(_('status'), max_length=2,
                              choices=make_choices(AmbulanceStatus),
                              default=AmbulanceStatus.UK.name)

    # location
    orientation = models.FloatField(_('orientation'), default=0.0)
    location = models.PointField(_('location'), srid=4326, default=defaults['location'])

    # timestamp
    timestamp = models.DateTimeField(_('timestamp'), default=timezone.now)

    # location client
    location_client = models.ForeignKey('login.Client',
                                        on_delete=models.CASCADE,
                                        blank=True, null=True,
                                        related_name='location_client_set',
                                        verbose_name=_('location_client'))

    # default value for _loaded_values
    _loaded_values = None

    @classmethod
    def from_db(cls, db, field_names, values):

        # call super
        instance = super(Ambulance, cls).from_db(db, field_names, values)

        # store the original field values on the instance
        instance._loaded_values = dict(zip(field_names, values))

        # return instance
        return instance

    def save(self, *args, **kwargs):

        # creation?
        created = self.pk is None

        # loaded_values?
        loaded_values = self._loaded_values is not None

        # create equipment holder?
        try:
            if created or self.equipmentholder is None:
                self.equipmentholder = EquipmentHolder.objects.create()
        except EquipmentHolder.DoesNotExist:
            self.equipmentholder = EquipmentHolder.objects.create()

        # has location changed?
        has_moved = False
        if (not loaded_values) or \
                calculate_distance(self._loaded_values['location'], self.location) > stationary_radius:
            has_moved = True

        # calculate orientation only if location has changed and orientation has not changed
        if has_moved and loaded_values and self._loaded_values['orientation'] == self.orientation:
            # TODO: should we allow for a small radius before updating direction?
            self.orientation = calculate_orientation(self._loaded_values['location'], self.location)
            # logger.debug('< {} - {} = {}'.format(self._loaded_values['location'],
            #                                      self.location,
            #                                      self.orientation))

        # logger.debug('loaded_values: {}'.format(loaded_values))
        # logger.debug('_loaded_values: {}'.format(self._loaded_values))
        # logger.debug('self.location_client: {}'.format(self.location_client))

        # location_client changed?
        if self.location_client is None:
            location_client_id = None
        else:
            location_client_id = self.location_client.id
        location_client_changed = False
        if loaded_values and location_client_id != self._loaded_values['location_client_id']:
            location_client_changed = True

        # logger.debug('location_client_changed: {}'.format(location_client_changed))
        # TODO: Check if client is logged with ambulance if setting location_client

        # if comment, capability, status or location changed
        model_changed = False
        if has_moved or \
                self._loaded_values['status'] != self.status or \
                self._loaded_values['capability'] != self.capability or \
                self._loaded_values['comment'] != self.comment:

            # save to Ambulance
            super().save(*args, **kwargs)

            # logger.debug('SAVED')

            # save to AmbulanceUpdate
            data = {k: getattr(self, k)
                    for k in ('capability', 'status', 'orientation',
                              'location', 'timestamp',
                              'comment', 'updated_by', 'updated_on')}
            data['ambulance'] = self
            obj = AmbulanceUpdate(**data)
            obj.save()

            # logger.debug('UPDATE SAVED')

            # model changed
            model_changed = True

        # if identifier changed
        # NOTE: self._loaded_values is NEVER None because has_moved is True
        elif (location_client_changed or
              self._loaded_values['identifier'] != self.identifier):

            # save only to Ambulance
            super().save(*args, **kwargs)

            # logger.debug('SAVED')

            # model changed
            model_changed = True

        # Did the model change?
        if model_changed:

            # publish to mqtt
            from mqtt.publish import SingletonPublishClient
            SingletonPublishClient().publish_ambulance(self)

            # logger.debug('PUBLISHED ON MQTT')

        # just created?
        if created:
            # invalidate permissions cache
            from mqtt.cache_clear import mqtt_cache_clear
            mqtt_cache_clear()

    def delete(self, *args, **kwargs):

        # invalidate permissions cache
        from mqtt.cache_clear import mqtt_cache_clear
        mqtt_cache_clear()

        # delete from Ambulance
        super().delete(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('ambulance:detail', kwargs={'pk': self.id})

    def __str__(self):
        return ('Ambulance {}(id={}) ({}) [{}]:\n' +
                '    Status: {}\n' +
                '  Location: {} @ {}\n' +
                ' LocClient: {}\n' +
                '   Updated: {} by {}').format(self.identifier,
                                               self.id,
                                               AmbulanceCapability[self.capability].value,
                                               self.comment,
                                               AmbulanceStatus[self.status].value,
                                               self.location,
                                               self.timestamp,
                                               self.location_client,
                                               self.updated_by,
                                               self.updated_on)


class AmbulanceUpdate(UpdatedByHistoryModel):

    # ambulance
    ambulance = models.ForeignKey(Ambulance,
                                  on_delete=models.CASCADE,
                                  verbose_name=_('ambulance'))

    # ambulance capability
    capability = models.CharField(_('capability'), max_length=1,
                                  choices=make_choices(AmbulanceCapability))

    # ambulance status
    status = models.CharField(_('status'), max_length=2,
                              choices=make_choices(AmbulanceStatus),
                              default=AmbulanceStatus.UK.name)

    # location
    orientation = models.FloatField(_('orientation'), default=0.0)
    location = models.PointField(_('location'), srid=4326, default=defaults['location'])

    # timestamp, indexed
    timestamp = models.DateTimeField(_('timestamp'), db_index=True, default=timezone.now)

    class Meta:
        indexes = [
            models.Index(
                fields=['ambulance', 'timestamp'],
                name='ambulance_timestamp_idx',
            ),
        ]


# Call related models

class CallPriority(Enum):
    A = _('Resuscitation')
    B = _('Emergent')
    C = _('Urgent')
    D = _('Less urgent')
    E = _('Not urgent')
    O = _('Omega')


CallPriorityOrder = [ 
    CallPriority.A,
    CallPriority.B,
    CallPriority.C,
    CallPriority.D,
    CallPriority.E,
    CallPriority.O,
] 


class CallStatus(Enum):
    P = _('Pending')
    S = _('Started')
    E = _('Ended')


CallStatusOrder = [ 
    CallStatus.P,
    CallStatus.S,
    CallStatus.E
] 


class PublishMixin:

    def save(self, *args, **kwargs):

        # publish?
        publish = kwargs.pop('publish', True)

        # save to Call
        super().save(*args, **kwargs)

        if publish:
            self.publish()


class Call(PublishMixin,
           UpdatedByModel):

    # status
    status = models.CharField(_('status'), max_length=1,
                              choices=make_choices(CallStatus),
                              default=CallStatus.P.name)

    # details
    details = models.CharField(_('details'), max_length=500, default="")

    # call priority
    priority = models.CharField(_('priority'), max_length=1,
                                choices=make_choices(CallPriority),
                                default=CallPriority.E.name)

    # timestamps
    pending_at = models.DateTimeField(_('pending_at'), null=True, blank=True)
    started_at = models.DateTimeField(_('started_at'), null=True, blank=True)
    ended_at = models.DateTimeField(_('ended_at'), null=True, blank=True)

    # created at
    created_at = models.DateTimeField(_('created_at'), auto_now_add=True)

    def save(self, *args, **kwargs):

        if self.status == CallStatus.E.name:

            # timestamp
            self.ended_at = timezone.now()

        elif self.status == CallStatus.S.name:

            # timestamp
            self.started_at = timezone.now()

        elif self.status == CallStatus.P.name:

            # timestamp
            self.pending_at = timezone.now()

        # call super
        super().save(*args, **kwargs)

    def publish(self, **kwargs):

        # publish to mqtt
        from mqtt.publish import SingletonPublishClient
        SingletonPublishClient().publish_call(self, **kwargs)

    def abort(self):

        # simply return if already ended
        if self.status == CallStatus.E.name:
            return

        # retrieve all calls not completed
        not_completed_ambulancecalls = self.ambulancecall_set.exclude(status=AmbulanceCallStatus.C.name)

        if not_completed_ambulancecalls:
            # if  ambulancecalls, set ambulancecall to complete until all done

            for ambulancecall in not_completed_ambulancecalls:

                # change call status to completed
                ambulancecall.status = AmbulanceCallStatus.C.name
                ambulancecall.save()

            # At the last ambulance call will be closed

        else:
            # if no ambulancecalls, force abort

            # change call status to ended
            self.status = CallStatus.E.name
            self.save()

    def get_ambulances(self):
        return ', '.join(ac.ambulance.identifier for ac in self.ambulancecall_set.all())

    def __str__(self):
        return "{} ({})".format(self.status, self.priority)


class AmbulanceCallStatus(Enum):
    R = _('Requested')
    A = _('Accepted')
    D = _('Declined')
    S = _('Suspended')
    C = _('Completed')


class AmbulanceCall(PublishMixin,
                    UpdatedByModel):

    # status
    status = models.CharField(_('status'), max_length=1,
                              choices=make_choices(AmbulanceCallStatus),
                              default=AmbulanceCallStatus.R.name)

    # call
    call = models.ForeignKey(Call,
                             on_delete=models.CASCADE,
                             verbose_name=_('call'))

    # ambulance
    ambulance = models.ForeignKey(Ambulance,
                                  on_delete=models.CASCADE,
                                  verbose_name=_('ambulance'))

    # created at
    # created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):

        # retrieve call
        call = self.call

        # changed to accepted?
        if self.status == AmbulanceCallStatus.A.name:

            if call.status != CallStatus.S.name:

                # change call status to started
                call.status = CallStatus.S.name
                call.save(publish=False)

        # changed to complete?
        elif self.status == AmbulanceCallStatus.C.name:

            # retrieve all accepted ambulances
            accepted_ambulancecalls = call.ambulancecall_set.exclude(status=AmbulanceCallStatus.C.name)

            set_size = len(accepted_ambulancecalls)
            if (set_size == 0 or
                    (set_size == 1 and accepted_ambulancecalls[0].ambulance is not self)):

                logger.debug('This is the last ambulance; will end call.')

                # then change call status to ended
                call.status = CallStatus.E.name
                call.save(publish=False)

            else:

                logger.debug('There are still {} ambulances in this call.'.format(set_size))
                logger.debug(accepted_ambulancecalls)

        # changed to declined?
        elif self.status == AmbulanceCallStatus.D.name:

            logger.debug('Ambulance call declined.')

        # changed to suspended?
        elif self.status == AmbulanceCallStatus.S.name:

            logger.debug('Ambulance call suspended.')

        # call super
        super().save(*args, **kwargs)

        # call history save
        AmbulanceCallHistory.objects.create(ambulance_call=self, status=self.status,
                                            comment=self.comment,
                                            updated_by=self.updated_by, updated_on=self.updated_on)

        # publish call
        call.publish()

    def publish(self, **kwargs):

        # publish to mqtt
        from mqtt.publish import SingletonPublishClient
        SingletonPublishClient().publish_call_status(self, **kwargs)

    class Meta:
        unique_together = ('call', 'ambulance')


class AmbulanceCallHistory(UpdatedByHistoryModel):
    # ambulance_call
    ambulance_call = models.ForeignKey(AmbulanceCall,
                                       on_delete=models.CASCADE,
                                       verbose_name=_('ambulance_call'))

    # status
    status = models.CharField(_('status'), max_length=1,
                              choices=make_choices(AmbulanceCallStatus))

    # created at
    # created_at = models.DateTimeField()


# Patient, might be expanded in the future

class Patient(PublishMixin,
              models.Model):
    """
    A model that provides patient fields.
    """

    call = models.ForeignKey(Call,
                             on_delete=models.CASCADE,
                             verbose_name=_('call'))

    name = models.CharField(_('name'), max_length=254, default="")
    age = models.IntegerField(_('age'), null=True)

    def publish(self):

        # publish to mqtt
        from mqtt.publish import SingletonPublishClient
        SingletonPublishClient().publish_call(self.call)


# Location related models

# noinspection PyPep8
class LocationType(Enum):
    b = _('Base')
    a = _('AED')
    i = _('Incident')
    h = _('Hospital')
    w = _('Waypoint')
    o = _('Other')


LocationTypeOrder = [
    LocationType.h,
    LocationType.b,
    LocationType.a,
    LocationType.o,
    LocationType.i,
    LocationType.w
]


class Location(AddressModel,
               UpdatedByModel):

    # location name
    name = models.CharField(_('name'), max_length=254, blank=True)

    # location type
    type = models.CharField(_('type'), max_length=1,
                            choices=make_choices(LocationType))

    # location: already in address
    # location = models.PointField(srid=4326, null=True)

    def get_absolute_url(self):
        return reverse('ambulance:location_detail', kwargs={'pk': self.id})

    def __str__(self):
        return "{} @{} ({})".format(self.name, self.location, self.comment)


# Waypoint related models

class WaypointStatus(Enum):
    C = _('Created')
    V = _('Visiting')
    D = _('Visited')
    S = _('Skipped')


class Waypoint(PublishMixin,
               UpdatedByModel):
    # call
    ambulance_call = models.ForeignKey(AmbulanceCall,
                                       on_delete=models.CASCADE,
                                       verbose_name=_('ambulance_call'))

    # order
    order = models.PositiveIntegerField(_('order'))

    # status
    status = models.CharField(_('status'), max_length=1,
                              choices=make_choices(WaypointStatus),
                              default=WaypointStatus.C.name)

    # Location
    location = models.ForeignKey(Location,
                                 on_delete=models.CASCADE,
                                 blank=True, null=True,
                                 verbose_name=_('location'))

    def is_created(self):
        return self.status == WaypointStatus.C.name

    def is_visited(self):
        return self.status == WaypointStatus.D.name

    def is_visiting(self):
        return self.status == WaypointStatus.V.name

    def is_skipped(self):
        return self.status == WaypointStatus.S.name

    def save(self, *args, **kwargs):

        # call super
        super().save(*args, **kwargs)

        # waypoint history save
        WaypointHistory.objects.create(waypoint=self,
                                       order=self.order, status=self.status,
                                       comment=self.comment, updated_by=self.updated_by, updated_on=self.updated_on)

    def publish(self, **kwargs):

        # logger.debug('Will publish')

        # publish to mqtt
        from mqtt.publish import SingletonPublishClient
        SingletonPublishClient().publish_call(self.ambulance_call.call)


class WaypointHistory(UpdatedByModel):
    # waypoint
    waypoint = models.ForeignKey(Waypoint,
                                 on_delete=models.CASCADE,
                                 verbose_name=_('waypoint'))

    # order
    order = models.PositiveIntegerField(_('order'))

    # status
    status = models.CharField(_('status'), max_length=1,
                              choices=make_choices(WaypointStatus))


# THOSE NEED REVIEWING

class Region(models.Model):
    name = models.CharField(_('name'), max_length=254, unique=True)
    center = models.PointField(_('center'), srid=4326, null=True)

    def __str__(self):
        return self.name
