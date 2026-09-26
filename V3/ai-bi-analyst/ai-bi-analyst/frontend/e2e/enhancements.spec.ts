import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'

const SAMPLE = path.resolve(__dirname, '../../backend/samples/retail_orders.csv')

async function uploadSample(page: Page): Promise<void> {
  await page.goto('/')
  await page.setInputFiles('input[type="file"]', SAMPLE)
  await page
    .getByLabel('What is this data, and who reads the report?')
    .fill('Monthly order extract. Regional directors review revenue and margin by channel.')
  await page.getByRole('button', { name: 'Profile this file' }).click()
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
}

async function selectColumns(page: Page, labels: string[]): Promise<void> {
  const picker = page.getByTestId('column-select')
  for (const label of labels) {
    await picker.click()
    await picker.fill(label)
    await page.getByRole('option').first().click()
  }
}

test.describe('ENH-01 model and token usage panel', () => {
  test('is visible on every route and names the narrator the backend used', async ({ page }) => {
    await uploadSample(page)
    const panel = page.getByRole('region', { name: 'Model and token usage' })

    for (const route of ['Overview', 'Data profile', 'Relationships', 'Report ideas', 'Chart advisor', 'Ask the data', 'Storyboard']) {
      await page.getByRole('link', { name: new RegExp(route) }).click()
      await expect(panel).toBeVisible()
      await expect(panel.getByText('rule-based-narrator')).toBeVisible()
    }

    // With no key configured the built-in narrator answers and no tokens accrue.
    await expect(panel.getByText('Not used yet')).toBeVisible()
    await expect(panel.getByText('0 / 0')).toBeVisible()

    await panel.getByRole('button', { name: 'Usage details' }).click()
    await expect(page.getByText(/Not billing data/)).toBeVisible()
    await expect(page.getByText(/api[_-]?key/i)).toHaveCount(0)
  })
})

test.describe('ENH-02 Data Profile summary tiles', () => {
  test('shows the same four numbers as Overview', async ({ page }) => {
    await uploadSample(page)

    const read = async (label: string) => {
      const tile = page.locator('div', { has: page.getByText(label, { exact: true }) }).first()
      return (await tile.textContent()) ?? ''
    }
    const overviewRows = await read('Total rows')
    const overviewColumns = await read('Total columns')
    const overviewDuplicates = await read('Duplicate rows')
    const overviewMissing = await read('Missing cells')

    await page.getByRole('link', { name: /Data profile/ }).click()
    await expect(page.getByRole('heading', { name: 'Data profile' })).toBeVisible()
    expect(await read('Total rows')).toBe(overviewRows)
    expect(await read('Total columns')).toBe(overviewColumns)
    expect(await read('Duplicate rows')).toBe(overviewDuplicates)
    expect(await read('Missing cells')).toBe(overviewMissing)
  })
})

test.describe('ENH-03 Chart Advisor selection consistency', () => {
  test('every badge, count and preview follows the current selection', async ({ page }) => {
    await uploadSample(page)
    await page.getByRole('link', { name: /Chart advisor/ }).click()

    await selectColumns(page, ['Revenue amount', 'Region'])
    await expect(page.getByTestId('selection-fingerprint')).toContainText('revenue_amount › region')
    await expect(page.getByLabel(/2 selected/)).toBeVisible()
    await expect(page.getByTestId('selection-summary')).toContainText('Revenue amount')
    await expect(page.getByTestId('selection-summary')).toContainText('Region')

    // A two-measure chart cannot be offered for one measure.
    const options = page.getByTestId('valid-options')
    await expect(options).toContainText('bar')
    await expect(options).not.toContainText('scatter')

    // Adding a second measure makes scatter valid, and the count follows.
    await selectColumns(page, ['Margin amount'])
    await expect(page.getByLabel(/3 selected/)).toBeVisible()
    await expect(options).toContainText('scatter')

    // Removing a column must remove every card that referenced it.
    await page.getByTestId('column-select').press('Backspace')
    await expect(page.getByLabel(/2 selected/)).toBeVisible()
    await expect(page.getByTestId('selection-summary')).not.toContainText('Margin amount')
    await expect(options).not.toContainText('scatter')
  })

  test('a late response for an older selection is not rendered', async ({ page }) => {
    await uploadSample(page)
    await page.getByRole('link', { name: /Chart advisor/ }).click()

    // Delay the first advice call so it can only resolve after the second.
    let seen = 0
    await page.route('**/chart-advice**', async (route) => {
      seen += 1
      if (seen === 1) await new Promise((resolve) => setTimeout(resolve, 2500))
      await route.continue()
    })

    await selectColumns(page, ['Revenue amount'])
    await selectColumns(page, ['Region'])

    await expect(page.getByTestId('selection-summary')).toContainText('Region')
    await page.waitForTimeout(3000)
    // The slow first response must not overwrite the current two-column result.
    await expect(page.getByTestId('selection-summary')).toContainText('Region')
    await expect(page.getByLabel(/2 selected/)).toBeVisible()
  })

  test('a column that is not in the dataset is reported, not silently used', async ({ page }) => {
    await uploadSample(page)
    await page.getByRole('link', { name: /Chart advisor/ }).click()
    await selectColumns(page, ['Revenue amount'])
    await expect(page.getByTestId('valid-options')).toBeVisible()
    await expect(page.getByText('Distribution of Cost')).toHaveCount(0)
  })
})

test.describe('ENH-04 Quick Ask actions', () => {
  test('every suggestion runs the governed flow and renders an answer', async ({ page }) => {
    await uploadSample(page)
    await page.getByRole('link', { name: /Ask the data/ }).click()

    const chips = page.getByTestId('quick-asks').getByRole('button')
    const count = await chips.count()
    expect(count).toBeGreaterThan(3)

    const categories = new Set<string>()
    for (let index = 0; index < count; index += 1) {
      const chip = page.getByTestId('quick-asks').getByRole('button').nth(index)
      categories.add((await chip.getAttribute('data-category')) ?? '')
      await chip.click()
      await expect(page.getByText('The plan that ran')).toBeVisible()
      await expect(page.getByText('Evidence')).toBeVisible()
      await expect(page.getByText(/Planned from a structured Quick Ask intent/)).toBeVisible()
    }
    expect(categories.size).toBeGreaterThanOrEqual(5)
  })

  test('a suggestion is keyboard operable', async ({ page }) => {
    await uploadSample(page)
    await page.getByRole('link', { name: /Ask the data/ }).click()
    const chip = page.getByTestId('quick-asks').getByRole('button').first()
    await chip.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByText('The plan that ran')).toBeVisible()
  })

  test('a failed Quick Ask stays on screen with a retry action', async ({ page }) => {
    await uploadSample(page)
    await page.getByRole('link', { name: /Ask the data/ }).click()

    let fail = true
    await page.route('**/questions**', async (route) => {
      if (fail) {
        fail = false
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ error: 'internal_error', detail: 'Injected failure', request_id: 'test' }),
        })
        return
      }
      await route.continue()
    })

    await page.getByTestId('quick-asks').getByRole('button').first().click()
    await expect(page.getByText('Injected failure')).toBeVisible()
    await page.getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByText('The plan that ran')).toBeVisible()
  })
})

test.describe('ENH-05 state across navigation', () => {
  test('returning to Chart Advisor keeps the selection and does not re-analyse', async ({ page }) => {
    await uploadSample(page)

    let profileCalls = 0
    await page.route('**/api/v1/datasets', async (route) => {
      if (route.request().method() === 'POST') profileCalls += 1
      await route.continue()
    })
    let adviceCalls = 0
    await page.route('**/chart-advice**', async (route) => {
      adviceCalls += 1
      await route.continue()
    })

    await page.getByRole('link', { name: /Chart advisor/ }).click()
    await selectColumns(page, ['Revenue amount', 'Region'])
    await expect(page.getByTestId('valid-options')).toBeVisible()
    const callsAfterSelect = adviceCalls

    await page.getByRole('link', { name: /Relationships/ }).click()
    await expect(page.getByRole('heading', { name: 'Relationships' })).toBeVisible()
    await page.getByRole('link', { name: /Chart advisor/ }).click()

    await expect(page.getByLabel(/2 selected/)).toBeVisible()
    await expect(page.getByTestId('selection-fingerprint')).toContainText('revenue_amount › region')
    expect(adviceCalls).toBe(callsAfterSelect) // served from the query cache
    expect(profileCalls).toBe(0) // no duplicate dataset profiling
  })

  test('a deliberate refresh restores the saved selection for this dataset', async ({ page }) => {
    await uploadSample(page)
    await page.getByRole('link', { name: /Chart advisor/ }).click()
    await selectColumns(page, ['Revenue amount', 'Region'])
    await expect(page.getByTestId('valid-options')).toBeVisible()

    await page.waitForTimeout(700) // allow the debounced save
    await page.reload()

    await expect(page.getByLabel(/2 selected/)).toBeVisible()
    await expect(page.getByTestId('selection-fingerprint')).toContainText('revenue_amount › region')
  })
})

test.describe('ENH-06 dataset-scoped cache', () => {
  test('two uploads with the same filename stay separate', async ({ page, request }) => {
    await uploadSample(page)
    const firstId = new URL(page.url()).pathname.split('/')[2]
    const firstRows = await page.getByText(/rows ·/).textContent()

    await uploadSample(page)
    const secondId = new URL(page.url()).pathname.split('/')[2]
    expect(secondId).not.toBe(firstId)

    const first = await (await request.get(`/api/v1/datasets/${firstId}/cache`)).json()
    const second = await (await request.get(`/api/v1/datasets/${secondId}/cache`)).json()
    expect(first.present).toBe(true)
    expect(second.present).toBe(true)
    expect(first.dataset_id).not.toBe(second.dataset_id)
    expect(firstRows).toBeTruthy()
  })

  test('deleting a session removes the dataset and its cache', async ({ page, request }) => {
    await uploadSample(page)
    const datasetId = new URL(page.url()).pathname.split('/')[2]
    await page.getByRole('button', { name: 'Delete session' }).click()

    await expect(page.getByRole('button', { name: 'Profile this file' })).toBeVisible()
    const report = await request.get(`/api/v1/datasets/${datasetId}/cache`)
    expect(report.status()).toBe(404)
  })
})
