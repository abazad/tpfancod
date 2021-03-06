#! /usr/bin/python2.7
# -*- coding: utf8 -*-
#
# tpfanco - controls the fan-speed of IBM/Lenovo ThinkPad Notebooks
# Copyright (C) 2011-2014 Vladyslav Shtabovenko
# Copyright (C) 2007-2009 Sebastian Urban
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import logging
import dbus.service
import gobject


class UnavailableException(dbus.DBusException):
    _dbus_error_name = 'org.tpfanco.UnavailableException'


class Control(dbus.service.Object):

    """fan controller"""

    # value that temperature has to fall below to slow down fan
    current_trip_temps = {}
    # current fan speeds required by sensor readings
    current_trip_speeds = {}
    # last spinup time for interval cooling mode
    last_interval_spinup = 0
    # fan in interval cooling mode
    #interval_mode = False
    # fan on in interval cooling mode
    #interval_running = False

    def __init__(self, bus, path, act_settings):
        self.act_settings = act_settings
        self.logger = logging.getLogger(__name__)

        if self.act_settings.debug:
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.ERROR)

        self.logger.debug(
            'Profile comment: ' + str(self.act_settings.profile_comment))
        self.logger.debug(
            'Hysteresis: ' + str(self.act_settings.hysteresis))
        self.logger.debug(
            'Trigger points: ' + str(self.act_settings.trigger_points))
        self.logger.debug(
            'Sensor names: ' + str(self.act_settings.sensor_names))

        dbus.service.Object.__init__(self, bus, path)
        self.repoll(1)

    def set_speed(self, speed):
        """sets the fan speed (0=off, 2-8=normal, 254=disengaged, 255=ec, 256=full-speed)"""
        fan_state = self.get_fan_state()
        try:
            self.logger.debug(
                'Rearming fan watchdog timer (+' + str(self.act_settings.watchdog_time) + ' s)')
            self.logger.debug(
                'Current fan level is ' + str(fan_state['level']))
            fanfile = open(self.act_settings.ibm_fan, 'w')
            fanfile.write('watchdog %d' % self.act_settings.watchdog_time)
            fanfile.flush()
            if speed == fan_state['level']:
                self.logger.debug('-> Keeping the current fan level unchanged')
            else:
                self.logger.debug('-> Setting fan level to ' + str(speed))
                if speed == 0:
                    fanfile.write('disable')
                else:
                    fanfile.write('enable')
                    fanfile.flush()
                    if speed == 254:
                        fanfile.write('level disengaged')
                    if speed == 255:
                        fanfile.write('level auto')
                    elif speed == 256:
                        fanfile.write('level full-speed')
                    else:
                        fanfile.write('level %d' % (speed - 1))
            fanfile.flush()
        except IOError:
            # sometimes write fails during suspend/resume
            pass
        finally:
            try:
                fanfile.close()
            except:
                pass

    @dbus.service.method('org.tpfanco.tpfancod.Control', in_signature='', out_signature='s')
    def get_version(self):
        return self.act_settings.version

    @dbus.service.method('org.tpfanco.tpfancod.Control', in_signature='', out_signature='a{si}')
    def get_temperatures(self):
        """returns list of current sensor readings, +/-128 or 0 means sensor is disconnected"""
        res = {}
        # TODO: we need to be able to read the sensors even if fan control is
        # disabled
        try:
            tempfile = open(self.act_settings.ibm_thermal, 'r')
            elements = tempfile.readline().split()[1:]
            tempfile.close()
            for idx, val in enumerate(elements):
                if str(idx) in self.act_settings.trigger_points:
                    res[str(idx)] = val
        except IOError, e:
            # sometimes read fails during suspend/resume
            if self.act_settings.trigger_points == {}:
                raise UnavailableException(e.message)
            else:
                pass
        finally:
            try:
                tempfile.close()
            except:
                pass
        # now we need to loop through hwmon sensors
        for sensor in self.act_settings.trigger_points:
            # string are assumed to be from hwmon, while ints are from
            # ibm_thermal
            if not sensor.isdigit():
                try:
                    tempfile = open(sensor, 'r')
                    element = tempfile.readline()
                    tempfile.close()
                    scaling = self.act_settings.sensor_scalings[sensor]
                    # need to convert the value of the sensor to degree
                    # Celsius
                    res[sensor] = int(
                        round(float(element.strip()) * float(scaling)))
                except IOError, e:
                    # sometimes read fails during suspend/resume
                    raise UnavailableException(e.message)
                finally:
                    try:
                        tempfile.close()
                    except:
                        pass
        res = {str(x): int(y) for x, y in res.items()}
        self.logger.debug('Output of get_temperatures ' + str(res))
        return res

    @dbus.service.method('org.tpfanco.tpfancod.Control', in_signature='', out_signature='a{si}')
    def get_fan_state(self):
        """Returns current (fan_level, fan_rpm)"""
        try:
            fanfile = open(self.act_settings.ibm_fan, 'r')
            for line in fanfile.readlines():
                key, value = line.split(':')
                if key == 'speed':
                    rpm = int(value.strip())
                if key == 'level':
                    value = value.strip()
                    if value == '0':
                        level = 0
                    elif value == 'auto':
                        level = 255
                    elif value == 'disengaged' or value == 'full-speed':
                        level = 256
                    # Ugly stub for the removed interval mode
                    elif value == 1:
                        level = 2
                    else:
                        level = int(value) + 1
            # if act_settings.enabled and self.interval_mode:
            #    level = 1
            return {'level': level,
                    'rpm': rpm}
        except Exception, e:
            raise UnavailableException(e.message)
        finally:
            try:
                fanfile.close()
            except:
                pass

    @dbus.service.method('org.tpfanco.tpfancod.Control', in_signature='', out_signature='')
    def reset_trips(self):
        """resets current trip points, should be called after config change"""
        self.current_trip_speeds = {}
        self.current_trip_temps = {}

    @dbus.service.method('org.tpfanco.tpfancod.Control', in_signature='', out_signature='a{si}')
    def get_trip_temperatures(self):
        """returns the current hysteresis temperatures for all sensors"""
        return self.current_trip_temps

    @dbus.service.method('org.tpfanco.tpfancod.Control', in_signature='', out_signature='a{si}')
    def get_trip_fan_speeds(self):
        """returns the current hysteresis fan speeds for all sensors"""
        return self.current_trip_speeds

    def repoll(self, interval):
        """calls poll again after interval msecs"""
        ival = int(interval)
        # ensure limits
        # i.e. make sure that we always repoll before the watchdog timer runs
        # out
        if ival < 1:
            ival = 1
        if ival > self.act_settings.watchdog_time * 1000:
            ival = self.act_settings.watchdog_time * 1000

        gobject.timeout_add(ival, self.poll)

    def poll(self):
        """main fan control routine"""
        # get the current fan level
        fan_state = self.get_fan_state()
        self.logger.debug('')
        self.logger.debug('Polling the sensors')
        self.logger.debug(
            'Current fan level: ' + str(fan_state['level']) + ' (' + str(fan_state['rpm']) + ' RPM)')
        # fan control is activated only if it is enabled and there is a profile
        # or the user specified to override existing profile
        if (self.act_settings.enabled and self.act_settings.is_profile_exactly_matched()) or (self.act_settings.enabled and self.act_settings.override_profile):
            self.logger.debug('Fan control enabled')
            try:
                temps = self.get_temperatures()
            except UnavailableException:
                # temperature read failed
                self.set_speed(255)
                self.repoll(self.act_settings.poll_time)
                return False
            new_speed = 0
            # check that we have at least one temperature sensor to monitor
            if len(temps) != 0:

                for tid in temps:
                    # we look only at the sensors that are listed in the
                    # profile
                    if tid in self.act_settings.trigger_points and tid.isdigit():
                        self.logger.debug(
                            'ibm_thermal sensor: ' + tid + ' has value ' + str(temps[tid]))
                        temp = temps[tid]
                        # value is +/-128 or 0, if sensor is disconnected
                        if abs(temp) != 128 and abs(temp) != 0:
                            points = self.act_settings.trigger_points[tid]
                            speed = 0
                            self.logger.debug(
                                'Sensor ' + str(tid) + ': ' + str(temp))
                            # check if temperature is above hysteresis shutdown
                            # point
                            if tid in self.current_trip_temps:
                                if temp >= self.current_trip_temps[tid]:
                                    speed = self.current_trip_speeds[tid]
                                else:
                                    del self.current_trip_temps[tid]
                                    del self.current_trip_speeds[tid]

                            # check if temperature is over trigger point
                            for trigger_temp, trigger_speed in points.iteritems():
                                if temp >= trigger_temp and speed < trigger_speed:
                                    self.current_trip_temps[
                                        tid] = trigger_temp - self.act_settings.hysteresis
                                    self.current_trip_speeds[
                                        tid] = trigger_speed
                                    speed = trigger_speed

                            new_speed = max(new_speed, speed)

                # we look only at the sensors that are listed in the profile
                for sensor in temps:
                    if not sensor.isdigit():
                        self.logger.debug(
                            'hwmon sensor: ' + sensor + ' has value ' + str(temps[sensor]))
                        temp = temps[sensor]
                        # value is 0, if sensor is disconnected
                        points = self.act_settings.trigger_points[sensor]
                        speed = 0
                        # check if temperature is above hysteresis shutdown
                        # point
                        if sensor in self.current_trip_temps:
                            if temp >= self.current_trip_temps[sensor]:
                                speed = self.current_trip_speeds[sensor]
                            else:
                                del self.current_trip_temps[sensor]
                                del self.current_trip_speeds[sensor]

                        # check if temperature is over trigger point
                        for trigger_temp, trigger_speed in points.iteritems():
                            if temp >= trigger_temp and speed < trigger_speed:
                                self.current_trip_temps[
                                    sensor] = trigger_temp - self.act_settings.hysteresis
                                self.current_trip_speeds[
                                    sensor] = trigger_speed
                                speed = trigger_speed

                        new_speed = max(new_speed, speed)
                self.logger.debug(
                    'Trying to set fan level to ' + str(new_speed) + ':')
            else:
                self.logger.debug(
                    'No sensors to monitor, giving fan control back to the EC control ')
                new_speed = 255
            # set fan speed
            self.set_speed(new_speed)
            self.repoll(self.act_settings.poll_time)
        else:
            self.logger.debug('Fan control disabled')
            self.set_speed(255)
            self.repoll(self.act_settings.poll_time)

        # remove current timer
        return False
