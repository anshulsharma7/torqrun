import "@testing-library/jest-dom/vitest";

// jsdom does not implement layout APIs.
Element.prototype.scrollIntoView = function scrollIntoView() {};
