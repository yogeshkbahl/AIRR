import { expect, test } from '@playwright/test'
import path from 'node:path'

const SAMPLE = path.resolve(__dirname, '../../backend/samples/retail_orders.csv')

test.describe('end to end discovery workflow', () => {
  test('profile a file, drill a finding, pin a chart and export', async ({ page }) => {
    await page.goto('/')
    await page.setInputFiles('input[type="file"]', SAMPLE)
    await page
      .getByLabel('What is this data, and who reads the report?')
      .fill('Monthly order extract. Regional directors review revenue and margin by channel.')
    await page.getByRole('button', { name: 'Profile this file' }).click()

    await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
    await expect(page.getByText('Data quality score')).toBeVisible()
    await expect(page.getByText(/rows ·/)).toBeVisible()

    await page.getByRole('link', { name: /Data profile/ }).click()
    await expect(page.getByRole('heading', { name: 'Data profile' })).toBeVisible()
    await expect(page.getByText('Findings')).toBeVisible()
    const showRows = page.getByRole('button', { name: 'Show affected rows' }).first()
    await showRows.click()
    await expect(page.getByText(/Hidden from this preview|No rows to show/)).toBeVisible()

    await page.getByRole('link', { name: /Report ideas/ }).click()
    await expect(page.getByRole('tab', { name: /Easy/ })).toBeVisible()

    await page.getByRole('link', { name: /Chart advisor/ }).click()
    const columnPicker = page.getByTestId('column-select')
    await columnPicker.click()
    await columnPicker.fill('Revenue amount')
    await page.getByRole('option').first().click()
    await columnPicker.fill('Region')
    await page.getByRole('option').first().click()
    await expect(page.getByText('Valid for this selection')).toBeVisible()
    await page.getByRole('button', { name: 'Add to storyboard' }).click()
    await expect(page.getByRole('button', { name: 'Added' })).toBeVisible()

    await page.getByRole('link', { name: /Ask the data/ }).click()
    await page.getByLabel('Your question').fill('What is total revenue by region?')
    await page.getByRole('button', { name: 'Ask' }).click()
    await expect(page.getByText('The plan that ran')).toBeVisible()
    await page.getByRole('button', { name: 'Show the query that ran' }).click()
    await expect(page.getByText(/GROUP BY/)).toBeVisible()

    await page.getByRole('link', { name: /Storyboard/ }).click()
    await expect(page.getByText('Executive page checklist')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Export definition' })).toBeVisible()
  })
})
