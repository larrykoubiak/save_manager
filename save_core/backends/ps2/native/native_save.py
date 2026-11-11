#
# This file is part of mymc+, based on mymc by Ross Ridge.
#
# mymc+ is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# mymc+ is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with mymc+.  If not, see <http://www.gnu.org/licenses/>.
#

"""Private PS2 directory/file bundle used by the filesystem implementation."""

class PS2SaveFile(object):
    """The state of a PlayStation 2 save file."""

    def __init__(self):
        self.file_ents = None
        self.file_data = None
        self.dirent = None


    def set_directory(self, ent):
        self.file_ents = [None] * ent[2]
        self.file_data = [None] * ent[2]
        self.dirent = list(ent)


    def set_file(self, i, ent, data):
        self.file_ents[i] = ent
        self.file_data[i] = data


    def get_directory(self):
        return self.dirent


    def get_file(self, i):
        return self.file_ents[i], self.file_data[i]


    def __len__(self):
        return self.dirent[2]


    def __getitem__(self, index):
        return self.get_file(index)
