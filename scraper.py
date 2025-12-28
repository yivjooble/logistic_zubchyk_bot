"""PUESC SENT-406 scraper using Playwright."""
import asyncio
import re
import logging
from dataclasses import dataclass
from typing import Optional
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)


@dataclass
class LocationResult:
    success: bool
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_time: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[str] = None


class PUESCScraper:
    """Scraper for PUESC SENT-406 geolocation check."""

    PUESC_URL = "https://puesc.gov.pl/uslugi/przewoz-towarow-objety-monitorowaniem/rmpd-406"
    TIMEOUT = 60000  # 60 seconds

    def __init__(self):
        self.browser = None
        self.playwright = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    async def start(self):
        """Start the browser."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
            ]
        )

    async def stop(self):
        """Stop the browser."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def check_location(
        self,
        reference_number: str,
        registration_number: str,
        locator_id: str
    ) -> LocationResult:
        """
        Check vehicle location on PUESC SENT-406.

        Args:
            reference_number: SENT/RMPD reference number
            registration_number: Vehicle registration number
            locator_id: GPS locator ID

        Returns:
            LocationResult with coordinates and timestamp or error
        """
        if not self.browser:
            await self.start()

        context = await self.browser.new_context(
            locale='pl-PL',
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = await context.new_page()

        try:
            # Navigate to the form page
            logger.info(f"Navigating to {self.PUESC_URL}")
            await page.goto(self.PUESC_URL, wait_until='domcontentloaded', timeout=self.TIMEOUT)
            logger.info("Page loaded, waiting for React to render...")

            # Wait for the form to load (React-based, needs time)
            await page.wait_for_timeout(3000)

            # Dismiss cookie banner if present
            try:
                cookie_close = await page.query_selector('button[aria-label="close"], .cookie-close, button:has-text("×"), button:has-text("Zamknij")')
                if cookie_close:
                    await cookie_close.click()
                    logger.info("Cookie banner dismissed")
                    await page.wait_for_timeout(500)
            except Exception:
                pass  # Cookie banner might not be present

            # Save screenshot for debugging
            await page.screenshot(path="/app/data/page_loaded.png")
            logger.info("Screenshot saved to /app/data/page_loaded.png")

            # Try to find and fill the form fields
            logger.info("Looking for form fields...")
            selectors = await self._find_form_selectors(page)

            if not selectors:
                # Save screenshot for debugging
                screenshot_path = "/app/data/debug_screenshot.png"
                await page.screenshot(path=screenshot_path)
                logger.error(f"Could not find form fields. Screenshot saved to {screenshot_path}")

                # Get page HTML for debugging
                content = await page.content()
                logger.error(f"Page content length: {len(content)}")

                return LocationResult(
                    success=False,
                    error="Could not find form fields. Page structure may have changed.",
                    raw_response=content[:2000]
                )

            logger.info(f"Found {len([k for k in selectors if k != 'submit'])} input elements")

            # Fill in the form - elements are already resolved
            try:
                ref_input = selectors['reference']
                reg_input = selectors['registration']
                loc_input = selectors['locator']

                # Click and fill each input
                await ref_input.click()
                await ref_input.fill(reference_number)
                logger.info(f"Filled reference number: {reference_number}")

                await reg_input.click()
                await reg_input.fill(registration_number)
                logger.info(f"Filled registration number: {registration_number}")

                await loc_input.click()
                await loc_input.fill(locator_id)
                logger.info(f"Filled locator ID: {locator_id}")

            except Exception as fill_error:
                logger.error(f"Error filling form: {fill_error}")
                await page.screenshot(path="/app/data/fill_error.png")
                return LocationResult(
                    success=False,
                    error=f"Could not fill form: {str(fill_error)}"
                )

            # Screenshot after filling
            await page.screenshot(path="/app/data/form_filled.png")
            logger.info("Form filled, looking for submit button...")

            # Submit the form
            submit_btn = await page.query_selector(selectors['submit'])
            if submit_btn:
                await submit_btn.click()
                logger.info("Form submitted, waiting for results...")
            else:
                # Try alternative submit methods
                logger.warning("Submit button not found, trying Enter key...")
                await loc_input.press('Enter')

            # Wait for results
            await page.wait_for_timeout(5000)

            # Screenshot after waiting for results
            await page.screenshot(path="/app/data/result.png", full_page=True)
            logger.info("Result screenshot saved")

            # Extract location data
            result = await self._extract_location_data(page)
            logger.info(f"Result: success={result.success}")
            return result

        except PlaywrightTimeout as e:
            logger.error(f"Timeout error: {e}")
            return LocationResult(
                success=False,
                error="Timeout: Page took too long to respond"
            )
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            return LocationResult(
                success=False,
                error=f"Error: {str(e)}"
            )
        finally:
            await context.close()

    async def _find_form_selectors(self, page) -> Optional[dict]:
        """
        Find form field selectors on the page.
        The PUESC page uses a React/Liferay portal with dynamic IDs.
        """
        # Get all visible text inputs on the page
        inputs = await page.query_selector_all('input[type="text"]')
        logger.info(f"Found {len(inputs)} text inputs on page")

        # The PUESC SENT-406 form has 3 main text inputs in order:
        # 1. Reference number (NUMER REFERENCYJNY)
        # 2. Registration number (NUMER REJESTRACYJNY)
        # 3. Locator ID (NUMER URZĄDZENIA/LOKALIZATORA)

        if len(inputs) >= 3:
            # Direct element approach - find inputs and interact directly
            return {
                'reference': inputs[0],
                'registration': inputs[1],
                'locator': inputs[2],
                'submit': 'button[type="submit"], button:has-text("Sprawdź"), button:has-text("Wyślij"), .btn-primary'
            }

        return None

    async def _extract_location_data(self, page) -> LocationResult:
        """Extract location data from the page after form submission."""
        # Get the page content for analysis
        content = await page.content()

        # Save HTML for debugging
        with open("/app/data/result.html", "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"HTML saved, length: {len(content)}")

        # Get visible text content
        text_content = await page.inner_text('body')
        logger.info(f"Page text (first 500 chars): {text_content[:500]}")

        # Try to find location data in the page
        # Patterns based on actual PUESC RMPD-406 response format
        coord_patterns = [
            # Latitude: 54.3873020000 / Longitude: 18.7060410000
            r'Latitude[:\s]+(\d{1,2}\.\d+)',
            r'Longitude[:\s]+(\d{1,3}\.\d+)',
        ]

        # Time patterns - format: 28.12.2025, h.19:25:59
        time_patterns = [
            r'Time[:\s]+(\d{2}\.\d{2}\.\d{4}[,\s]+h\.\d{2}:\d{2}:\d{2})',  # Time: 28.12.2025, h.19:25:59
            r'(\d{2}\.\d{2}\.\d{4}[,\s]+h\.\d{2}:\d{2}:\d{2})',  # 28.12.2025, h.19:25:59
            r'(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})',  # 2024-01-15 14:30:00
            r'(\d{2}[./-]\d{2}[./-]\d{4}\s+\d{2}:\d{2})',  # 15.01.2024 14:30
        ]

        latitude = None
        longitude = None
        location_time = None

        # Try to extract latitude (Polish: Szerokość geograficzna / English: Latitude)
        lat_patterns = [
            r'Szerokość geograficzna[:\s]+(\d{1,2}\.\d+)',
            r'Szerokość[:\s]+(\d{1,2}\.\d+)',
            r'Latitude[:\s]+(\d{1,2}\.\d+)',
        ]
        for pattern in lat_patterns:
            lat_match = re.search(pattern, text_content, re.IGNORECASE)
            if lat_match:
                latitude = float(lat_match.group(1))
                logger.info(f"Found latitude: {latitude}")
                break

        # Try to extract longitude (Polish: Długość geograficzna / English: Longitude)
        lon_patterns = [
            r'Długość geograficzna[:\s]+(\d{1,3}\.\d+)',
            r'Długość[:\s]+(\d{1,3}\.\d+)',
            r'Longitude[:\s]+(\d{1,3}\.\d+)',
        ]
        for pattern in lon_patterns:
            lon_match = re.search(pattern, text_content, re.IGNORECASE)
            if lon_match:
                longitude = float(lon_match.group(1))
                logger.info(f"Found longitude: {longitude}")
                break

        # Try to extract time (Polish: Czas / godz. / English: Time)
        time_patterns = [
            r'Czas[:\s]+(\d{2}\.\d{2}\.\d{4}[,\s]+godz\.\d{2}:\d{2}:\d{2})',
            r'Time[:\s]+(\d{2}\.\d{2}\.\d{4}[,\s]+h\.\d{2}:\d{2}:\d{2})',
            r'(\d{2}\.\d{2}\.\d{4}[,\s]+godz\.\d{2}:\d{2}:\d{2})',
            r'(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})',
        ]
        for pattern in time_patterns:
            match = re.search(pattern, text_content, re.IGNORECASE)
            if match:
                location_time = match.group(1).strip()
                # Replace Polish "godz." with Ukrainian "год."
                location_time = location_time.replace('godz.', 'год.')
                logger.info(f"Found time: {location_time}")
                break

        # If we found coordinates, return success immediately
        if latitude and longitude:
            return LocationResult(
                success=True,
                latitude=latitude,
                longitude=longitude,
                location_time=location_time
            )

        # Only check for error messages if we didn't find coordinates
        error_patterns = [
            r'brak danych',
            r'nie znaleziono',
            r'nieprawidłow',
        ]

        for pattern in error_patterns:
            if re.search(pattern, text_content, re.IGNORECASE):
                error_match = re.search(
                    rf'({pattern}[^<\n]{{0,100}})',
                    text_content,
                    re.IGNORECASE
                )
                error_msg = error_match.group(1) if error_match else "Location data not found"
                return LocationResult(
                    success=False,
                    error=error_msg
                )

        return LocationResult(
            success=False,
            error="Could not extract location data from response"
        )


async def check_vehicle_location(
    reference_number: str,
    registration_number: str,
    locator_id: str
) -> LocationResult:
    """
    Convenience function to check location without managing browser lifecycle.
    """
    async with PUESCScraper() as scraper:
        return await scraper.check_location(
            reference_number,
            registration_number,
            locator_id
        )


# Test function
async def main():
    """Test the scraper with sample data."""
    result = await check_vehicle_location(
        reference_number="TEST123",
        registration_number="WB12345",
        locator_id="GPS001"
    )
    print(f"Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
