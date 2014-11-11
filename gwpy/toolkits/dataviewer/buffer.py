# coding=utf-8
# Copyright (C) Duncan Macleod (2014)
#
# This file is part of GWDV
#
# GWDV is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GWDV is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GWDV.  If not, see <http://www.gnu.org/licenses/>

"""This module defines the `DataBuffer`
"""

import abc
import operator

try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict

from gwpy.detector import ChannelList
from gwpy.segments import (Segment, SegmentList, DataQualityFlag)
from gwpy.time import to_gps
from gwpy.timeseries import (TimeSeries, TimeSeriesList, TimeSeriesDict,
                             StateVector, StateVectorDict)
from gwpy.spectrogram import (Spectrogram, SpectrogramList)

from . import version
from .log import Logger
from .source import DataSourceMeta


# -----------------------------------------------------------------------------
#
# Core objects
#
# -----------------------------------------------------------------------------

class BufferCore(object):
    """Fancy data holder with auto-fetch for data

    This class is a stub, and will not work on its own; all developers
    should sub class this object and override the :meth:`fetch` method
    to take in a :class:`~gwpy.detector.ChannelList`, and GPS start and
    end times, and return a :class:`~gwpy.timeseries.TimeSeriesDict`
    from whichever remote data source they are programming for.
    """

    # data classes for reading from remote source
    RawSeriesClass = TimeSeries
    RawDictClass = TimeSeriesDict

    # data classes for buffer storage
    ListClass = TimeSeriesList
    SeriesClass = TimeSeries
    DictClass = TimeSeriesDict

    def __init__(self, channels, logger=Logger('buffer'), **kwargs):
        """Create a new `DataBuffer`
        """
        if isinstance(channels, str):
            channels = [channels]
        self.channels = ChannelList.from_names(*channels)
        self.data = self.DictClass()
        self.logger = logger

    def get(self, segments=None, fetch=True, **fetchargs):
        """Return data for the given segments

        This method will fetch any data as required, then return a coalesced
        set of data.

        Parameters
        ----------
        channel : `str`, `list`
            one channel name, or a list of channel names whose data you want
        segments : `SegmentList`, optional
            the data segments during which data are required
        fetch : `bool`, optional
            retrieve more data from the remote source, default: `True`,
            otherwise just return those data already in the buffer that
            match the `segments`
        **fetchargs
            other keyword arguments used when fetching data from the remote
            source

        Returns
        -------
        data : `dict` of :class:`~gwpy.timeseries.TimeSeriesList`
            (channel, `TimeSeriesList`) `dict` with data for those
            segments requested
        """
        # no times given, return what data we have
        if segments is None:
            return type(self.data)((c, self.data[c]) for c in channels)
        elif isinstance(segments, (Segment, tuple)):
            segments = SegmentList([Segment(map(to_gps, segments))])
        elif isinstance(segments, DataQualityFlag):
            segments = segments.active
        # otherwise, check the data we have
        available = reduce(
            operator.and_, (self.data[c].segments for c in self.channels))
        new = segments - available

        # -- get new data -----------------------
        if fetch and abs(new):
            for seg in new:
                data = self.fetch(self.channels, seg[0], seg[1], **fetchargs)
                self.append(data)
                self.coalesce()

        # -- return the requested data ----------
        out = type(self.data)()
        for channel in self.channels:
            data = self.SeriesClass()
            for ts in self.data[channel]:
                for seg in segments:
                    if abs(seg) < ts.dt.value:
                        continue
                    if ts.span.intersects(seg):
                        cropped = ts.crop(float(seg[0]), float(seg[1]),
                                          copy=False)
                        if cropped.size:
                            data.append(cropped)
            out[channel] = data.coalesce()
        if isinstance(channel, str) and len(channels) == 1:
            return out[channels[0]]
        else:
            return out

    def coalesce(self):
        """Coalesce the data held within this `DataBuffer`
        """
        for key, data in self.data.iteritems():
            self.data[key] = data.coalesce()

    def append(self, new, **kwargs):
        """Append data to this `DataBuffer`
        """
        self.data.append(new, **kwargs)

    def fetch(self, segments, **kwargs):
        raise NotImplementedError(
            "fetch() method must be overwritten by subclass.")

    def iterate(self, **kwargs):
        raise NotImplementedError(
            "iterate() method must be overwritten by subclass.")

    # -------------------------------------------------------------------------
    # data properties/methods

    def data(self):
        """The data held within this `DataBuffer`

        This property should be overridden by all subclasses to return
        the correct data.
        """
        return self._data

    # -------------------------------------------------------------------------
    # timing properties

    @property
    def segments(self):
        """The segments during which data have been fetched

        :type: `~gwpy.segments.SegmentList`
        """
        try:
            return reduce(
                operator.or_, (SegmentList([tsl.span]) for tsl in
                    self.data.values()))
        except TypeError:
            return SegmentList()

    @property
    def extent(self):
        """The enclosing segment during which data have been fetched

        .. warning::

           Thie `extent` does not guarantee that all data in the middle
           have been fetched, gaps may be present depending on which
           segments were used

        :type: `~gwpy.segments.Segment`
        """
        return Segment(*self.segments.extent())

    @property
    def start(self):
        """The GPS start time of data in the buffer
        """
        return self.extent[0]

    @property
    def start(self, t):
        if t >= self.end:
            raise ValueError("Cannot set start time after current end time")
        elif t >= self.start:
            warnings.warn("Existing start time is before new start time, "
                          "nothing will be done")
        else:
            self.fetch(self.channels, t, self.start)

    @property
    def end(self):
        """The GPS end time of data in the buffer
        """
        return self.extent[1]

    @property
    def end(self, t):
        if t <= self.start:
            raise ValueError("Cannot set end time before current start time")
        elif t <= self.end:
            warnings.warn("Existing end time is after new end time, "
                          "nothing will be done")
        else:
            self.fetch(self.channels, self.end, t)

    # -------------------------------------------------------------------------
    # channel properties

    def add_channels(self, *channels, **fetchargs):
        """Add one of more channels to this `DataBuffer`

        Parameters
        ----------
        *channels : `str`, `~gwpy.detector.Channel`
            one or more channels to add to the buffer. Any channels that
            already exist in the buffer will be ignored
        **fetchargs
            keyword arguments to pass to the `fetch()` method.
        """
        # find new channels
        channels = ChannelList.from_names(channels)
        new = []
        for c in channels:
            if c not in self.channels:
                new.append(c)
                self.channels.append(c)
        # fetch data for new channels
        for seg in self.segments:
            self.fetch(new, seg[0], seg[1], **fetchargs)


# -----------------------------------------------------------------------------
#
# User-interface objects
#
# -----------------------------------------------------------------------------

class DataBuffer(BufferCore):
    __metaclass__ = DataSourceMeta


class DataIterator(BufferCore):
    __metaclass__ = DataSourceMeta

    def __iter__(self):
        return self