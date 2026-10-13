use pyo3::prelude::*;

mod geometry;
mod archive;

#[pymodule]
fn pdf_to_epub_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(geometry::sort_reading_order, m)?)?;
    m.add_function(wrap_pyfunction!(geometry::detect_columns, m)?)?;
    m.add_function(wrap_pyfunction!(geometry::batch_sort_reading_order, m)?)?;
    m.add_function(wrap_pyfunction!(geometry::reconstruct_table_grid, m)?)?;
    m.add_function(wrap_pyfunction!(archive::build_epub_archive, m)?)?;
    Ok(())
}

