"""link helpers."""

from typing import Dict, List, Union
from urllib.parse import ParseResult, parse_qs, unquote, urlencode, urljoin, urlparse

import attr
from stac_pydantic.links import Relations
from stac_pydantic.shared import MimeTypes
from starlette.requests import Request

# These can be inferred from the item/collection so they aren't included in the database
# Instead they are dynamically generated when querying the database using the classes defined below
INFERRED_LINK_RELS = ["self", "item", "parent", "collection", "root"]


def filter_links(links: List[Dict]) -> List[Dict]:
    """Remove inferred links."""
    return [link for link in links if link["rel"] not in INFERRED_LINK_RELS]


def merge_params(url: str, newparams: Dict) -> str:
    """Merge url parameters."""
    u = urlparse(url)
    params = parse_qs(u.query)
    params.update(newparams)
    param_string = unquote(urlencode(params, True))

    href = ParseResult(
        scheme=u.scheme,
        netloc=u.netloc,
        path=u.path,
        params=u.params,
        query=param_string,
        fragment=u.fragment,
    ).geturl()
    return href


@attr.s
class BaseLinks:
    """Create inferred links common to collections and items."""

    request: Request = attr.ib()

    @property
    def base_url(self):
        """Get the base url."""
        return str(self.request.base_url)

    @property
    def url(self):
        """Get the current request url."""
        return str(self.request.url)

    def resolve(self, url):
        """Resolve url to the current request url."""
        return urljoin(str(self.base_url), str(url))

    def link_self(self) -> Dict:
        """Return the self link."""
        return {"rel": Relations.self, "type": MimeTypes.json, "href": self.url}

    def link_root(self) -> Dict:
        """Return the catalog root."""
        return {"rel": Relations.root, "type": MimeTypes.json, "href": self.base_url}

    def create_links(self) -> List[Dict]:
        """Return all inferred links."""
        links = []
        for name in dir(self):
            if name.startswith("link_") and callable(getattr(self, name)):
                link = getattr(self, name)()
                links.append(link)
        return links

    async def get_links(self, extra_links: List[Dict] = []) -> List[Dict]:
        """
        Generate all the links.

        Get the links object for a stac resource by iterating through
        available methods on this class that start with link_.
        """
        if self.request.method == "POST":
            self.request.postbody = await self.request.json()
        # join passed in links with generated links
        # and update relative paths
        links = self.create_links()
        if extra_links is not None:
            for link in extra_links:
                if link["rel"] not in INFERRED_LINK_RELS:
                    link["href"] = self.resolve(link["href"])
                    links.append(link)
        return links


@attr.s
class PagingLinks(BaseLinks):
    """Create links for paging."""

    next: str = attr.ib(kw_only=True, default=None)
    prev: str = attr.ib(kw_only=True, default=None)

    def link_next(self) -> Dict:
        """Create link for next page."""
        if self.next is not None:
            method = self.request.method
            if method == "GET":
                href = merge_params(self.url, {"token": f"next:{self.next}"})
                link = {
                    "rel": Relations.next,
                    "type": MimeTypes.json,
                    "method": method,
                    "href": href,
                }
                return link
            if method == "POST":
                body = self.request.postbody
                body["token"] = f"next:{self.next}"
                return {
                    "rel": Relations.next,
                    "type": MimeTypes.json,
                    "method": method,
                    "href": f"{self.request.url}",
                    "body": body,
                }

    def link_prev(self) -> Dict:
        """Create link for previous page."""
        if self.prev is not None:
            method = self.request.method
            if method == "GET":
                href = merge_params(self.url, {"token": f"prev:{self.prev}"})
                return {
                    "rel": Relations.previous,
                    "type": MimeTypes.json,
                    "method": method,
                    "href": href,
                }
            if method == "POST":
                body = self.request.postbody
                body["token"] = f"prev:{self.prev}"
                return {
                    "rel": Relations.previous,
                    "type": MimeTypes.json,
                    "method": method,
                    "href": f"{self.request.url}",
                    "body": body,
                }


@attr.s
class CollectionLinksBase(BaseLinks):
    """Create inferred links specific to collections."""

    collection_id: str = attr.ib()

    def collection_link(self, rel=Relations.collection) -> Dict:
        """Create a link to a collection."""
        return {
            "rel": rel,
            "type": MimeTypes.json,
            "href": self.resolve(f"collections/{self.collection_id}"),
        }


@attr.s
class CollectionLinks(CollectionLinksBase):
    """Create inferred links specific to collections."""

    def link_self(self) -> Dict:
        """Return the self link."""
        return self.collection_link(rel=Relations.self)

    def link_parent(self) -> Dict:
        """Create the `parent` link."""
        return {"rel": Relations.parent, "type": MimeTypes.json, "href": self.base_url}

    def link_items(self) -> Dict:
        """Create the `item` link."""
        return {
            "rel": "items",
            "type": MimeTypes.geojson,
            "href": self.resolve(f"collections/{self.collection_id}/items"),
        }


@attr.s
class ItemLinks(CollectionLinksBase):
    """Create inferred links specific to items."""

    item_id: str = attr.ib()

    def link_self(self) -> Dict:
        """Create the self link."""
        return {
            "rel": Relations.self,
            "type": MimeTypes.geojson,
            "href": self.resolve(
                f"collections/{self.collection_id}/items/{self.item_id}"
            ),
        }

    def link_parent(self) -> Dict:
        """Create the `parent` link."""
        return self.collection_link(rel=Relations.parent)

    def link_collection(self) -> Dict:
        """Create the `collection` link."""
        return self.collection_link()

    def link_tiles(self) -> Dict:
        """Create the `tiles` link."""
        return {
            "rel": Relations.alternate,
            "type": MimeTypes.json,
            "title": "tiles",
            "href": self.resolve(
                f"collections/{self.collection_id}/items/{self.item_id}/tiles",
            ),
        }


@attr.s
class TileLinks:
    """Create inferred links specific to OGC Tiles API."""

    base_url: str = attr.ib()
    collection_id: str = attr.ib()
    item_id: str = attr.ib()

    def __post_init__(self):
        """Post init handler."""
        self.item_uri = urljoin(
            self.base_url, f"collections/{self.collection_id}/items/{self.item_id}",
        )

    def link_tiles(self) -> Dict:
        """Create tiles link."""
        return {
            "href": urljoin(
                self.base_url,
                f"titiler/tiles/{{z}}/{{x}}/{{y}}.png?url={self.item_uri}",
            ),
            "rel": Relations.item,
            "title": "tiles",
            "type": MimeTypes.png,
            "templated": True,
        }

    def link_viewer(self) -> Dict:
        """Create viewer link."""
        return {
            "href": urljoin(self.base_url, f"titiler/viewer?url={self.item_uri}"),
            "rel": Relations.alternate,
            "type": MimeTypes.html,
            "title": "viewer",
        }

    def link_tilejson(self) -> Dict:
        """Create tilejson link."""
        return {
            "href": urljoin(
                self.base_url, f"titiler/tilejson.json?url={self.item_uri}"
            ),
            "rel": Relations.alternate,
            "type": MimeTypes.json,
            "title": "tilejson",
        }

    def link_wmts(self) -> Dict:
        """Create wmts capabilities link."""
        return {
            "href": urljoin(
                self.base_url, f"titiler/WMTSCapabilities.xml?url={self.item_uri}",
            ),
            "rel": Relations.alternate,
            "type": MimeTypes.xml,
            "title": "WMTS Capabilities",
            "templated": False,
        }
