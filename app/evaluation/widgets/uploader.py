import re
import uuid

from collections import Iterable
from datetime import timedelta

from io import IOBase

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.forms.widgets import Widget
from django.http.request import HttpRequest
from django.http.response import HttpResponseBadRequest, \
    JsonResponse, HttpResponseForbidden
from django.template.loader import get_template
from django.utils import timezone


from evaluation.models import StagedFile


def cleanup_stale_files():
    """
    Cleanup routine target function to be invoked repeatedly. It scans the
    database for stale uploaded files and deletes them.
    """
    now = timezone.now()
    files_to_delete = StagedFile.objects.filter(timeout__lt=now).all()
    for file in files_to_delete:
        print(f"Deleting {file.id}...")
        file.delete()


class NotFoundError(Exception): pass
class InvalidRequestException(Exception): pass


class AjaxUploadWidget(Widget):
    """
    A widget that implements asynchronous file uploads for forms. It creates
    a list of database ids and adds them to the form using AJAX requests.

    To use this widget, a website must fulfill certain requirements:
     - The following JavaScript libraries must be loaded:
       - jQuery (3.2.1)
       - jQuery-ui (1.12.1)
       - blueimp-file-upload (9.19.1)
     - The website must include the JS and CSS files defined in the classes
       variables CSS and JS
     - The website must define a djang csfr-token by either:
       - defining a hidden input element with the name 'csrfmiddlewaretoken'
         (use the {% csrf_token %} template function for this).
       - define the csfr_token by defining the global javascript variable
         'upload_csrf_token'
     - For each widget a valid ajax-receiver must be installed. Each instance
       of an AjaxUploadWidget exposes the function 'handle_ajax' as handler
       for ajax requests. During initialization, the ajax-path must be
       defined using the 'ajax_target_path' named parameter
     - Add cleanup service call to cleanup_stale_files in a background worker

    Notes
    -----
    There are potential security risks with the implementation. First of all,
    uploads are not linked to any session or similar. Anyone who can guess
    a valid database id referring to a file, can also refer to this file. What
    this means depends on the actual app that uses this widget.

    This widget will require updating when moving forward from django 1.8.
    """

    CSS = "/static/evaluation/upload_widget.css"
    JS = "/static/evaluation/upload_widget.js"

    TEMPLATE_ATTRS = dict(JS=JS, CSS=CSS)

    def __init__(
            self,
            *args,
            ajax_target_path: str = None,
            **kwargs):
        super(AjaxUploadWidget, self).__init__(*args, **kwargs)

        if ajax_target_path is None:
            raise ValueError("AJAX target path required")

        self.ajax_target_path = ajax_target_path
        self.timeout = timedelta(hours=2)

    def _handle_complete(
            self,
            request: HttpRequest,
            csrf_token: str,
            uploaded_file: UploadedFile) -> dict:
        new_staged_file = StagedFile.objects.create(
            csrf=csrf_token,
            client_id=None,
            client_filename=uploaded_file.name,

            file_id=uuid.uuid4(),
            timeout=timezone.now() + self.timeout,

            file=uploaded_file,
            start_byte=0,
            end_byte=uploaded_file.size - 1,
            total_size=uploaded_file.size,
        )

        return {
            "filename": new_staged_file.client_filename,
            "uuid": new_staged_file.file_id,
            "extra_attrs": {},
        }

    def _handle_chunked(
            self,
            request: HttpRequest,
            csrf_token: str,
            uploaded_file: UploadedFile) -> dict:
        # Only content ranges of the form
        #
        #   bytes-unit SP byte-range-resp
        #
        # according to rfc7233 are accepted. See here:
        # https://tools.ietf.org/html/rfc7233#appendix-C
        range_header = request.META.get("HTTP_CONTENT_RANGE", None)
        if not range_header:
            raise InvalidRequestException(
                "Client did not supply Content-Range")
        range_match = re.match(
            r"bytes (?P<start>[0-9]{1,32})-(?P<end>[0-9]{1,32})/(?P<length>\*|[0-9]{1,32})",
            range_header)
        if not range_header:
            raise InvalidRequestException("Supplied invalid Content-Range")
        start_byte = int(range_match.group("start"))
        end_byte = int(range_match.group("end"))
        if range_match.group("length") is None:
            total_size = None
        else:
            total_size = int(range_match.group("length"))
        if start_byte > end_byte:
            raise InvalidRequestException("Supplied invalid Content-Range")
        if (total_size is not None) and (end_byte >= total_size):
            raise InvalidRequestException("End byte exceeds total file size")
        if end_byte - start_byte + 1 != uploaded_file.size:
            raise InvalidRequestException("Invalid start-end byte range")

        client_id = request.META.get(
            "X-Upload-ID",
            request.POST.get(
                "X-Upload-ID",
                None))
        if not client_id:
            raise InvalidRequestException(
                "Client did not supply a X-Upload-ID")
        if len(client_id) > 128:
            raise InvalidRequestException("X-Upload-ID is too long")

        # Verify consistency and generate file ids
        other_chunks = StagedFile.objects.filter(
            csrf=csrf_token, client_id=client_id).all()
        if len(other_chunks) == 0:
            file_id = uuid.uuid4()
        else:
            chunk_intersects = other_chunks.filter(
                start_byte__lte=end_byte, end_byte__gte=start_byte).exists()
            if chunk_intersects:
                raise InvalidRequestException("Overlapping chunks")

            inconsisent_filenames = other_chunks.exclude(
                client_filename=uploaded_file.name).exists()
            if inconsisent_filenames:
                raise InvalidRequestException(
                    "Chunks have inconsistent filenames")

            if total_size is not None:
                inconsistent_total_size = other_chunks.exclude(
                    total_size=None).exclude(
                    total_size=total_size).exists()
                if inconsistent_total_size:
                    raise InvalidRequestException("Inconsistent total size")

            file_id = other_chunks[0].file_id

        new_staged_file = StagedFile.objects.create(
            csrf=csrf_token,
            client_id=client_id,
            client_filename=uploaded_file.name,

            file_id=file_id,
            timeout=timezone.now() + self.timeout,

            file=uploaded_file,
            start_byte=start_byte,
            end_byte=end_byte,
            total_size=total_size,
        )

        return {
            "filename": new_staged_file.client_filename,
            "uuid": new_staged_file.file_id,
            "extra_attrs": {},
        }

    def handle_ajax(self, request: HttpRequest):
        if request.method != "POST":
            return HttpResponseBadRequest()

        csrf_token = request.META.get('CSRF_COOKIE', None)
        if not csrf_token:
            return HttpResponseForbidden("CSRF token is missing")

        if "HTTP_CONTENT_RANGE" in request.META:
            handler = self._handle_chunked
        else:
            handler = self._handle_complete

        result = []
        try:
            for uploaded_file in request.FILES.values():
                result.append(handler(request, csrf_token, uploaded_file))
        except InvalidRequestException as e:
            return HttpResponseBadRequest(str(e))

        return JsonResponse(result, safe=False)

    template = get_template("widgets/uploader.html")
    def render(self, name, value, attrs=None):

        if isinstance(value, Iterable):
            value = ",".join(str(x) for x in value)
        elif value in (None, ""):
            value = ""
        else:
            value = str(value)

        context = {
            "target": self.ajax_target_path,
            "value": value,
            "name": name,
            "attrs": attrs,
        }

        return template.render(context=context)


class IntervalMap:
    def __init__(self):
        self.__endpoints = []

    def append_interval(self, length, label):
        self.__endpoints.append((len(self) + length, label))
        self.__endpoints.sort()

    def __find_endpoint_index(self, i):
        def find(start, end):
            # use nested intervals to find correct label
            if start == end:
                return start
            else:
                mid = (start + end) // 2
                if self.__endpoints[mid][0] > i:
                    return find(start, mid)
                else:
                    return find(mid + 1, end)
        if i >= len(self):
            return None
        else:
            return find(0, len(self.__endpoints))

    def get_offset(self, i):
        endpoint_index = self.__find_endpoint_index(i)
        if endpoint_index is None:
            return None
        elif endpoint_index == 0:
            return 0
        else:
            return self.__endpoints[endpoint_index - 1][0]

    def __getitem__(self, i):
        endpoint_index = self.__find_endpoint_index(i)
        if endpoint_index is None:
            return None
        else:
            return self.__endpoints[endpoint_index][1]

    def __len__(self):
        return self.__endpoints[-1][0] if self.__endpoints else 0


class OpenedStagedAjaxFile(IOBase):
    def __init__(self, _uuid):
        super(OpenedStagedAjaxFile, self).__init__()

        self.__uuid = _uuid

        self.__chunks = list(
            StagedFile.objects.filter(file_id=self.__uuid).all())
        self.__chunks.sort(key=lambda x: x.start_byte)

        self.__chunk_map = IntervalMap()
        for chunk in self.__chunks:
            self.__chunk_map.append_interval(
                chunk.end_byte - chunk.start_byte + 1,
                chunk)

        self.__file_pointer = 0

        self.__current_chunk = None

    @property
    def closed(self):
        return self.__chunks is None

    @property
    def size(self):
        if self.closed:
            return None
        else:
            return len(self.__chunk_map)

    def readable(self, *args, **kwargs):
        return True

    def writable(self, *args, **kwargs):
        return False

    def seekable(self, *args, **kwargs):
        return True

    def read(self):
        return self.read(1)[0]

    def read(self, count):
        if self.closed:
            raise IOError('file closed')
        if not (0 <= self.__file_pointer < self.size):
            return EOFError('file ended')

        result = b""
        while len(result) < count:
            if self.__file_pointer >= len(self.__chunk_map):
                break

            this_chunk = self.__chunk_map[self.__file_pointer]
            if this_chunk is not self.__current_chunk:
                # we need to switch to a new chunk
                if self.__current_chunk is not None:
                    self.__current_chunk.file.close()
                    self.__current_chunk = None

                this_chunk.file.open('rb')
                this_chunk.file.seek(
                    self.__file_pointer - this_chunk.start_byte)
                self.__current_chunk = this_chunk

            read_size = min(
                count - len(result),
                self.__current_chunk.end_byte + 1 - self.__file_pointer)
            result += self.__current_chunk.file.read(read_size)
            self.__file_pointer += read_size

        return result

    def seek(self, offset, from_what=0):
        if self.closed:
            raise IOError('file closed')

        new_pointer = None
        if from_what == 0:
            new_pointer = offset
        elif from_what == 1:
            new_pointer = self.__file_pointer + offset
        elif from_what == 2:
            new_pointer = self.size + offset

        if not (0 <= new_pointer <= self.size):
            raise EOFError('new pointer outside file boundaries')

        self.__file_pointer = new_pointer
        return self.__file_pointer

    def tell(self, *args, **kwargs):
        if self.closed:
            raise IOError('file closed')
        return self.__file_pointer

    def close(self):
        if not self.closed:
            self.__chunks = None
            if self.__current_chunk is not None:
                self.__current_chunk.file.close()
                self.__current_chunk = None


class StagedAjaxFile:
    def __init__(self, _uuid: uuid.UUID):
        super(StagedAjaxFile, self).__init__()

        if not isinstance(_uuid, uuid.UUID):
            raise TypeError("uuid parameter must be uuid.UUID")
        self.__uuid = _uuid

    def _raise_if_missing(self):
        query = StagedFile.objects.filter(file_id=self.__uuid)
        if not query.exists():
            raise NotFoundError()
        return query

    @property
    def uuid(self):
        return self.__uuid

    @property
    def name(self):
        chunks_query = self._raise_if_missing()
        return chunks_query.first().client_filename

    @property
    def exists(self):
        return StagedFile.objects.filter(file_id=self.__uuid).exists()

    @property
    def size(self):
        chunks_query = self._raise_if_missing()
        chunks = chunks_query.all()
        if len(chunks) == 0:
            raise NotFoundError()

        remaining_size = None

        # Check if we want to verify some total size
        total_sized_chunks = chunks.exclude(total_size=None)
        if total_sized_chunks.exists():
            remaining_size = total_sized_chunks.first().total_size

        current_size = 0
        for chunk in sorted(chunks, key=lambda x: x.start_byte):
            if chunk.start_byte != current_size:
                return None
            current_size = chunk.end_byte + 1
            if remaining_size is not None:
                remaining_size -= chunk.end_byte - chunk.start_byte + 1

        if remaining_size is not None:
            if remaining_size != 0:
                return None

        return current_size

    @property
    def is_complete(self):
        if not StagedFile.objects.filter(file_id=self.__uuid).exists():
            return False
        return self.size is not None

    def open(self):
        if not self.is_complete:
            raise IOError("incomplete upload")
        return OpenedStagedAjaxFile(self.__uuid)

    def delete(self):
        query = self._raise_if_missing()
        query.delete()


class UploadedAjaxFileList(forms.Field):
    def to_python(self, value):
        allowed_characters = '0123456789abcdefABCDEF-,'
        if any(c for c in value if c not in allowed_characters):
            raise ValidationError(
                "UUID list includes invalid characters")

        split_items = value.split(",")
        uuids = []
        for s in split_items:
            try:
                uuids.append(uuid.UUID(s))
            except ValueError:
                raise ValidationError(
                    "Not a valid UUID: %(string)s",
                    {"string": s})

        return [StagedAjaxFile(uuid) for uuid in uuids]

    def prepare_value(self, value):
        # convert value to be stuffed into the html, this must be
        # implemented if we want to pre-populate upload forms
        return None
