"""This module contains the LangfuseMedia class, which is used to wrap media objects for upload to Langfuse."""

import base64
import hashlib
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Literal, Optional, Tuple, TypeVar, cast

import requests

if TYPE_CHECKING:
    from langfuse._client.client import Langfuse

from langfuse.api import MediaContentType
from langfuse.types import ParsedMediaReference

T = TypeVar("T")


class LangfuseMedia:
    """A class for wrapping media objects for upload to Langfuse.

    This class handles the preparation and formatting of media content for Langfuse,
    supporting both base64 data URIs and raw content bytes.

    Args:
        obj (Optional[object]): The source object to be wrapped. Can be accessed via the `obj` attribute.
        base64_data_uri (Optional[str]): A base64-encoded data URI containing the media content
            and content type (e.g., "data:image/jpeg;base64,/9j/4AAQ...").
        content_type (Optional[str]): The MIME type of the media content when providing raw bytes.
        content_bytes (Optional[bytes]): Raw bytes of the media content.
        file_path (Optional[str]): The path to the file containing the media content. For relative paths,
            the current working directory is used.

    Raises:
        ValueError: If neither base64_data_uri or the combination of content_bytes
            and content_type is provided.
    """

    obj: object

    _log = logging.getLogger(__name__)
    _content_bytes: Optional[bytes]
    _content_type: Optional[MediaContentType]
    _source: Optional[str]
    _media_id: Optional[str]

    def __init__(
        self,
        *,
        obj: Optional[object] = None,
        base64_data_uri: Optional[str] = None,
        content_type: Optional[MediaContentType] = None,
        content_bytes: Optional[bytes] = None,
        file_path: Optional[str] = None,
    ):
        """Initialize a LangfuseMedia object.

        Args:
            obj: The object to wrap.

            base64_data_uri: A base64-encoded data URI containing the media content
                and content type (e.g., "data:image/jpeg;base64,/9j/4AAQ...").
            content_type: The MIME type of the media content when providing raw bytes or reading from a file.
            content_bytes: Raw bytes of the media content.
            file_path: The path to the file containing the media content. For relative paths,
                the current working directory is used.
        """
        self.obj = obj

        if base64_data_uri is not None:
            parsed_data = self._parse_base64_data_uri(base64_data_uri)
            self._content_bytes, self._content_type = parsed_data
            self._source = "base64_data_uri"

        elif content_bytes is not None and content_type is not None:
            self._content_type = content_type
            self._content_bytes = content_bytes
            self._source = "bytes"
        elif (
            file_path is not None
            and content_type is not None
            and os.path.exists(file_path)
        ):
            self._content_bytes = self._read_file(file_path)
            self._content_type = content_type if self._content_bytes else None
            self._source = "file" if self._content_bytes else None
        else:
            self._log.error(
                "base64_data_uri, or content_bytes and content_type, or file_path must be provided to LangfuseMedia"
            )

            self._content_bytes = None
            self._content_type = None
            self._source = None

        self._media_id = self._get_media_id()

    def _read_file(self, file_path: str) -> Optional[bytes]:
        try:
            with open(file_path, "rb") as file:
                return file.read()
        except Exception as e:
            self._log.error(f"Error reading file at path {file_path}", exc_info=e)

            return None

    def _get_media_id(self) -> Optional[str]:
        content_hash = self._content_sha256_hash

        if content_hash is None:
            return None

        # Convert hash to base64Url
        url_safe_content_hash = content_hash.replace("+", "-").replace("/", "_")

        return url_safe_content_hash[:22]

    @property
    def _content_length(self) -> Optional[int]:
        return len(self._content_bytes) if self._content_bytes else None

    @property
    def _content_sha256_hash(self) -> Optional[str]:
        if self._content_bytes is None:
            return None

        sha256_hash_bytes = hashlib.sha256(self._content_bytes).digest()

        return base64.b64encode(sha256_hash_bytes).decode("utf-8")

    @property
    def _reference_string(self) -> Optional[str]:
        if self._content_type is None or self._source is None or self._media_id is None:
            return None

        return f"@@@langfuseMedia:type={self._content_type}|id={self._media_id}|source={self._source}@@@"

    @staticmethod
    def parse_reference_string(reference_string: str) -> ParsedMediaReference:
        """Parse a media reference string into a ParsedMediaReference.

        Example reference string:
            "@@@langfuseMedia:type=image/jpeg|id=some-uuid|source=base64_data_uri@@@"

        Args:
            reference_string: The reference string to parse.

        Returns:
            A TypedDict with the media_id, source, and content_type.

        Raises:
            ValueError: If the reference string is empty or not a string.
            ValueError: If the reference string does not start with "@@@langfuseMedia:type=".
            ValueError: If the reference string does not end with "@@@".
            ValueError: If the reference string is missing required fields.
        """
        if not reference_string:
            raise ValueError("Reference string is empty")

        if not isinstance(reference_string, str):
            raise ValueError("Reference string is not a string")

        if not reference_string.startswith("@@@langfuseMedia:type="):
            raise ValueError(
                "Reference string does not start with '@@@langfuseMedia:type='"
            )

        if not reference_string.endswith("@@@"):
            raise ValueError("Reference string does not end with '@@@'")

        content = reference_string[len("@@@langfuseMedia:") :].rstrip("@@@")

        # Split into key-value pairs
        pairs = content.split("|")
        parsed_data = {}

        for pair in pairs:
            key, value = pair.split("=", 1)
            parsed_data[key] = value

        # Verify all required fields are present
        if not all(key in parsed_data for key in ["type", "id", "source"]):
            raise ValueError("Missing required fields in reference string")

        return ParsedMediaReference(
            media_id=parsed_data["id"],
            source=parsed_data["source"],
            content_type=cast(MediaContentType, parsed_data["type"]),
        )

    def _parse_base64_data_uri(
        self, data: str
    ) -> Tuple[Optional[bytes], Optional[MediaContentType]]:
        # Example data URI: data:image/jpeg;base64,/9j/4AAQ...
        try:
            if not data or not isinstance(data, str):
                raise ValueError("Data URI is not a string")

            if not data.startswith("data:"):
                raise ValueError("Data URI does not start with 'data:'")

            header, actual_data = data[5:].split(",", 1)
            if not header or not actual_data:
                raise ValueError("Invalid URI")

            # Split header into parts and check for base64
            header_parts = header.split(";")
            if "base64" not in header_parts:
                raise ValueError("Data is not base64 encoded")

            # Content type is the first part
            content_type = header_parts[0]
            if not content_type:
                raise ValueError("Content type is empty")

            return base64.b64decode(actual_data), cast(MediaContentType, content_type)

        except Exception as e:
            self._log.error("Error parsing base64 data URI", exc_info=e)

            return None, None

    @staticmethod
    def resolve_media_references(
        *,
        obj: T,
        langfuse_client: "Langfuse",
        resolve_with: Literal["base64_data_uri"],
        max_depth: int = 10,
        content_fetch_timeout_seconds: int = 10,
    ) -> T:
        """Replace media reference strings in an object with base64 data URIs.

        This method recursively traverses an object (up to max_depth) looking for media reference strings
        in the format "@@@langfuseMedia:...@@@". When found, it (synchronously) fetches the actual media content using
        the provided Langfuse client and replaces the reference string with a base64 data URI.

        If fetching media content fails for a reference string, a warning is logged and the reference
        string is left unchanged.

        Args:
            obj: The object to process. Can be a primitive value, array, or nested object.
                If the object has a __dict__ attribute, a dict will be returned instead of the original object type.
            langfuse_client: Langfuse client instance used to fetch media content.
            resolve_with: The representation of the media content to replace the media reference string with.
                Currently only "base64_data_uri" is supported.
            max_depth: Optional. Default is 10. The maximum depth to traverse the object.

        Returns:
            A deep copy of the input object with all media references replaced with base64 data URIs where possible.
            If the input object has a __dict__ attribute, a dict will be returned instead of the original object type.

        Example:
            obj = {
                "image": "@@@langfuseMedia:type=image/jpeg|id=123|source=bytes@@@",
                "nested": {
                    "pdf": "@@@langfuseMedia:type=application/pdf|id=456|source=bytes@@@"
                }
            }

            result = await LangfuseMedia.resolve_media_references(obj, langfuse_client)

            # Result:
            # {
            #     "image": "data:image/jpeg;base64,/9j/4AAQSkZJRg...",
            #     "nested": {
            #         "pdf": "data:application/pdf;base64,JVBERi0xLjcK..."
            #     }
            # }
        """

        def traverse(obj: Any, depth: int) -> Any:
            if depth > max_depth:
                return obj

            # Handle string
            if isinstance(obj, str):
                regex = r"@@@langfuseMedia:.+?@@@"
                reference_string_matches = re.findall(regex, obj)
                if len(reference_string_matches) == 0:
                    return obj

                result = obj
                reference_string_to_media_content = {}

                for reference_string in reference_string_matches:
                    try:
                        parsed_media_reference = LangfuseMedia.parse_reference_string(
                            reference_string
                        )
                        media_data = langfuse_client.api.media.get(
                            parsed_media_reference["media_id"]
                        )
                        media_content = requests.get(
                            media_data.url, timeout=content_fetch_timeout_seconds
                        )
                        if not media_content.ok:
                            raise Exception("Failed to fetch media content")

                        base64_media_content = base64.b64encode(
                            media_content.content
                        ).decode()
                        base64_data_uri = f"data:{media_data.content_type};base64,{base64_media_content}"

                        reference_string_to_media_content[reference_string] = (
                            base64_data_uri
                        )
                    except Exception as e:
                        LangfuseMedia._log.warning(
                            f"Error fetching media content for reference string {reference_string}: {e}"
                        )
                        # Do not replace the reference string if there's an error
                        continue

                for (
                    ref_str,
                    media_content_str,
                ) in reference_string_to_media_content.items():
                    result = result.replace(ref_str, media_content_str)

                return result

            # Handle arrays
            if isinstance(obj, list):
                return [traverse(item, depth + 1) for item in obj]

            # Handle dictionaries
            if isinstance(obj, dict):
                return {key: traverse(value, depth + 1) for key, value in obj.items()}

            # Handle objects:
            if hasattr(obj, "__dict__"):
                return {
                    key: traverse(value, depth + 1)
                    for key, value in obj.__dict__.items()
                }

            return obj

        return cast(T, traverse(obj, 0))
