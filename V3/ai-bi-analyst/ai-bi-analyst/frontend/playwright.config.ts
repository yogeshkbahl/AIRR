import { defineConfig } from '@playwright/test'

/**
 * Critical path only: upload a file, read the profile, get a chart
 * recommendation, pin it, export the storyboard.
 * Start both servers first, or let Playwright start the frontend for you.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: { baseURL: 'http://localhost:5173', trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 60_000,
  },
})
