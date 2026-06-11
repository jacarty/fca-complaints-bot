import time

import requests


class FCAHandbookClient:
    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://api-handbook.fca.org.uk"

    def get_handbook_tree(self) -> list[dict]:

        url = f"{self.base_url}/Handbook/GetAllHandbook"

        response = self.session.get(url=url)
        response.raise_for_status()
        data = response.json()
        return data["Result"]["headers"]

    # Handles rate limiting (sleep between calls) and basic error handling
    def get_section_provisions(self, chapter_id: str, section_id: str) -> list[dict]:

        time.sleep(0.25)

        url = f"{self.base_url}/Handbook/GetAllHandBookProvisionsSortedOrderByChapter/{chapter_id}"
        params = {"sectionId": section_id, "IsDeleted": "false"}

        response = self.session.get(url=url, params=params)
        response.raise_for_status()
        data = response.json()
        return data["Result"]["provisions"]


if __name__ == "__main__":
    app = FCAHandbookClient()
    data = app.get_handbook_tree()
    print(data)
