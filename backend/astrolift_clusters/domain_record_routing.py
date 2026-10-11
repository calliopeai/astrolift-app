"""Bounded Route53 record metadata; associations do not observe health or traffic."""

import re
from decimal import Decimal

from astrolift_clusters.schema.domain_diagnostics import (
    ManagedDomainRecordCidrRouting,
    ManagedDomainRecordCoordinates,
    ManagedDomainRecordGeoLocation,
    ManagedDomainRecordGeoProximity,
    ManagedDomainRecordRouting,
)
from astrolift_clusters.schema.domain_diagnostics import (
    ManagedDomainRoutingPolicy as Policy,
)


def _text(raw, key, limit, pattern=None):
    if key not in raw:
        return None
    value = raw[key]
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= limit
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or pattern is not None
        and not re.fullmatch(pattern, value)
    ):
        raise ValueError("PROVIDER_ROUTING_METADATA_INVALID")
    return value


def _integer(raw, key, minimum, maximum):
    if key not in raw:
        return None
    value = raw[key]
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("PROVIDER_ROUTING_METADATA_INVALID")
    return value


def _object(raw, key, keys):
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, dict) or not value or set(value) - set(keys):
        raise ValueError("PROVIDER_ROUTING_METADATA_INVALID")
    return value


def route53_routing(raw):
    identifier = _text(raw, "SetIdentifier", 128)
    weight = _integer(raw, "Weight", 0, 255)
    region = _text(raw, "Region", 64, r"[a-z0-9-]+")
    failover = _text(raw, "Failover", 9, r"PRIMARY|SECONDARY")
    health = _text(raw, "HealthCheckId", 64, r"[A-Za-z0-9_-]+")
    traffic = _text(raw, "TrafficPolicyInstanceId", 36, r"[A-Za-z0-9_-]+")
    multi = raw.get("MultiValueAnswer")
    if "MultiValueAnswer" in raw and type(multi) is not bool:
        raise ValueError("PROVIDER_ROUTING_METADATA_INVALID")
    geo = _object(raw, "GeoLocation", {"ContinentCode", "CountryCode", "SubdivisionCode"})
    location = None
    if geo is not None:
        continent = _text(geo, "ContinentCode", 2, r"AF|AN|AS|EU|NA|OC|SA")
        country = _text(geo, "CountryCode", 2, r"[A-Z]{2}|\*")
        subdivision = _text(geo, "SubdivisionCode", 3, r"[A-Z]{2,3}")
        if continent and (country or subdivision) or subdivision and country != "US":
            raise ValueError("PROVIDER_ROUTING_METADATA_INVALID")
        location = ManagedDomainRecordGeoLocation(
            continent_code=continent, country_code=country, subdivision_code=subdivision
        )
    proximity = _object(raw, "GeoProximityLocation", {"AWSRegion", "LocalZoneGroup", "Coordinates", "Bias"})
    nearby = None
    if proximity is not None:
        aws_region = _text(proximity, "AWSRegion", 64, r"[a-z0-9-]+")
        local_zone = _text(proximity, "LocalZoneGroup", 64, r"[a-z0-9-]+")
        coordinates = _object(proximity, "Coordinates", {"Latitude", "Longitude"})
        point = None
        if coordinates is not None:
            latitude = _text(coordinates, "Latitude", 6, r"[-+]?[0-9]{1,2}(\.[0-9]{0,2})?")
            longitude = _text(coordinates, "Longitude", 7, r"[-+]?[0-9]{1,3}(\.[0-9]{0,2})?")
            if (
                latitude is None
                or longitude is None
                or abs(Decimal(latitude)) > 90
                or abs(Decimal(longitude)) > 180
            ):
                raise ValueError("PROVIDER_ROUTING_METADATA_INVALID")
            point = ManagedDomainRecordCoordinates(latitude=latitude, longitude=longitude)
        if sum(value is not None for value in (aws_region, local_zone, point)) != 1:
            raise ValueError("PROVIDER_ROUTING_METADATA_INVALID")
        nearby = ManagedDomainRecordGeoProximity(
            aws_region=aws_region,
            local_zone_group=local_zone,
            coordinates=point,
            bias=_integer(proximity, "Bias", -99, 99),
        )
    cidr = _object(raw, "CidrRoutingConfig", {"CollectionId", "LocationName"})
    network = None
    if cidr is not None:
        collection = _text(cidr, "CollectionId", 36, r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")
        name = _text(cidr, "LocationName", 16, r"[0-9A-Za-z_\-*]+")
        if collection is None or name is None:
            raise ValueError("PROVIDER_ROUTING_METADATA_INVALID")
        network = ManagedDomainRecordCidrRouting(collection_id=collection, location_name=name)
    policies = [
        policy
        for present, policy in (
            (weight is not None, Policy.WEIGHTED),
            (region is not None, Policy.LATENCY),
            (failover is not None, Policy.FAILOVER),
            (location is not None, Policy.GEOLOCATION),
            (multi is True, Policy.MULTIVALUE),
            (nearby is not None, Policy.GEOPROXIMITY),
            (network is not None, Policy.CIDR),
        )
        if present
    ]
    if len(policies) > 1:
        policy, reason = Policy.UNKNOWN, "AMBIGUOUS_ROUTING_POLICY"
    elif (policies and identifier is None) or (not policies and identifier is not None):
        policy, reason = Policy.UNKNOWN, "INCOMPLETE_ROUTING_POLICY"
    else:
        policy, reason = (policies[0] if policies else Policy.SIMPLE), "PROVIDER_METADATA_OBSERVED"
    return ManagedDomainRecordRouting(
        policy=policy,
        reason=reason,
        set_identifier=identifier,
        weight=weight,
        region=region,
        failover=failover,
        geo_location=location,
        geo_proximity=nearby,
        cidr=network,
        multi_value_answer=multi,
        health_check_id=health,
        traffic_policy_instance_id=traffic,
    )
