import '@testing-library/jest-dom/vitest'

// Plotly needs a canvas and URL.createObjectURL that jsdom does not provide.
if (!window.URL.createObjectURL) {
  window.URL.createObjectURL = () => 'blob:mock'
}
