use pyo3::prelude::*;
use pyo3::types::PyDict;
use rayon::prelude::*;

#[derive(Debug, Clone, Copy, PartialEq)]
#[allow(dead_code)]
pub struct BoundingBox {
    pub x0: f32,
    pub y0: f32,
    pub x1: f32,
    pub y1: f32,
}

impl BoundingBox {
    pub fn new(x0: f32, y0: f32, x1: f32, y1: f32) -> Self {
        Self { x0, y0, x1, y1 }
    }

    pub fn width(&self) -> f32 {
        (self.x1 - self.x0).max(0.0)
    }

    #[allow(dead_code)]
    pub fn height(&self) -> f32 {
        (self.y1 - self.y0).max(0.0)
    }
}

#[derive(Debug, Clone)]
pub struct TextBlock {
    pub bbox: BoundingBox,
    pub text: String,
    #[allow(dead_code)]
    pub original_index: usize,
}

fn extract_block(obj: &Bound<'_, PyAny>, index: usize) -> PyResult<TextBlock> {
    let bbox = if let Ok(bbox_item) = obj.get_item("bbox") {
        if let Ok(seq) = bbox_item.extract::<(f32, f32, f32, f32)>() {
            BoundingBox::new(seq.0, seq.1, seq.2, seq.3)
        } else if let Ok(list) = bbox_item.extract::<Vec<f32>>() {
            if list.len() >= 4 {
                BoundingBox::new(list[0], list[1], list[2], list[3])
            } else {
                BoundingBox::new(0.0, 0.0, 0.0, 0.0)
            }
        } else {
            BoundingBox::new(0.0, 0.0, 0.0, 0.0)
        }
    } else {
        let x0 = obj.get_item("x0").and_then(|v| v.extract::<f32>()).unwrap_or(0.0);
        let y0 = obj.get_item("y0").and_then(|v| v.extract::<f32>()).unwrap_or(0.0);
        let x1 = obj.get_item("x1").and_then(|v| v.extract::<f32>()).unwrap_or(x0);
        let y1 = obj.get_item("y1").and_then(|v| v.extract::<f32>()).unwrap_or(y0);
        BoundingBox::new(x0, y0, x1, y1)
    };

    let text = if let Ok(t) = obj.get_item("text") {
        t.extract::<String>().unwrap_or_default()
    } else {
        String::new()
    };

    Ok(TextBlock {
        bbox,
        text,
        original_index: index,
    })
}

pub fn detect_columns_internal(blocks: &[TextBlock], page_width: f32) -> Vec<Vec<usize>> {
    let non_empty: Vec<&TextBlock> = blocks.iter().filter(|b| !b.text.trim().is_empty()).collect();
    if non_empty.is_empty() {
        let mut all_indices: Vec<usize> = (0..blocks.len()).collect();
        all_indices.sort_by(|&i, &j| {
            blocks[i].bbox.y0.partial_cmp(&blocks[j].bbox.y0).unwrap_or(std::cmp::Ordering::Equal)
                .then(blocks[i].bbox.x0.partial_cmp(&blocks[j].bbox.x0).unwrap_or(std::cmp::Ordering::Equal))
        });
        return vec![all_indices];
    }

    if non_empty.iter().any(|b| b.bbox.width() > page_width * 0.55) {
        let mut all_indices: Vec<usize> = (0..blocks.len()).collect();
        all_indices.sort_by(|&i, &j| {
            blocks[i].bbox.y0.partial_cmp(&blocks[j].bbox.y0).unwrap_or(std::cmp::Ordering::Equal)
                .then(blocks[i].bbox.x0.partial_cmp(&blocks[j].bbox.x0).unwrap_or(std::cmp::Ordering::Equal))
        });
        return vec![all_indices];
    }

    let mut left_edges: Vec<f32> = non_empty.iter().map(|b| b.bbox.x0).collect();
    left_edges.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    left_edges.dedup_by(|a, b| (*a - *b).abs() < 1.0);

    let min_edge = left_edges.first().copied().unwrap_or(0.0);
    let max_edge = left_edges.last().copied().unwrap_or(0.0);
    if left_edges.len() < 2 || (max_edge - min_edge) < 80.0_f32.max(page_width * 0.12) {
        let mut all_indices: Vec<usize> = (0..blocks.len()).collect();
        all_indices.sort_by(|&i, &j| {
            blocks[i].bbox.y0.partial_cmp(&blocks[j].bbox.y0).unwrap_or(std::cmp::Ordering::Equal)
                .then(blocks[i].bbox.x0.partial_cmp(&blocks[j].bbox.x0).unwrap_or(std::cmp::Ordering::Equal))
        });
        return vec![all_indices];
    }

    let split = (min_edge + max_edge) / 2.0;
    let mut col0 = Vec::new();
    let mut col1 = Vec::new();

    for (idx, b) in blocks.iter().enumerate() {
        if b.bbox.x0 < split {
            col0.push(idx);
        } else {
            col1.push(idx);
        }
    }

    col0.sort_by(|&i, &j| {
        blocks[i].bbox.y0.partial_cmp(&blocks[j].bbox.y0).unwrap_or(std::cmp::Ordering::Equal)
            .then(blocks[i].bbox.x0.partial_cmp(&blocks[j].bbox.x0).unwrap_or(std::cmp::Ordering::Equal))
    });
    col1.sort_by(|&i, &j| {
        blocks[i].bbox.y0.partial_cmp(&blocks[j].bbox.y0).unwrap_or(std::cmp::Ordering::Equal)
            .then(blocks[i].bbox.x0.partial_cmp(&blocks[j].bbox.x0).unwrap_or(std::cmp::Ordering::Equal))
    });

    vec![col0, col1]
}

pub fn sort_reading_order_indices(blocks: &[TextBlock], page_width: f32) -> Vec<usize> {
    let columns = detect_columns_internal(blocks, page_width);
    let mut result = Vec::with_capacity(blocks.len());
    for col in columns {
        result.extend(col);
    }
    result
}

#[pyfunction]
#[pyo3(signature = (blocks, page_width=None))]
pub fn sort_reading_order(
    py: Python<'_>,
    blocks: Vec<Bound<'_, PyAny>>,
    page_width: Option<f32>,
) -> PyResult<Vec<Py<PyAny>>> {
    let mut extracted = Vec::with_capacity(blocks.len());
    for (i, obj) in blocks.into_iter().enumerate() {
        let block = extract_block(&obj, i)?;
        extracted.push((block, obj.unbind()));
    }

    let indices = py.detach(|| {
        let blocks_only: Vec<TextBlock> = extracted.iter().map(|(b, _)| b.clone()).collect();
        let width = page_width.unwrap_or_else(|| {
            blocks_only.iter().map(|b| b.bbox.x1).fold(612.0_f32, f32::max)
        });
        sort_reading_order_indices(&blocks_only, width)
    });

    let mut result = Vec::with_capacity(indices.len());
    for idx in indices {
        result.push(extracted[idx].1.clone_ref(py));
    }
    Ok(result)
}

#[pyfunction]
#[pyo3(signature = (blocks, page_width))]
pub fn detect_columns(
    py: Python<'_>,
    blocks: Vec<Bound<'_, PyAny>>,
    page_width: f32,
) -> PyResult<Vec<Vec<Py<PyAny>>>> {
    let mut extracted = Vec::with_capacity(blocks.len());
    for (i, obj) in blocks.into_iter().enumerate() {
        let block = extract_block(&obj, i)?;
        extracted.push((block, obj.unbind()));
    }

    let col_indices = py.detach(|| {
        let blocks_only: Vec<TextBlock> = extracted.iter().map(|(b, _)| b.clone()).collect();
        detect_columns_internal(&blocks_only, page_width)
    });

    let mut result = Vec::with_capacity(col_indices.len());
    for indices in col_indices {
        let mut col_list = Vec::with_capacity(indices.len());
        for idx in indices {
            col_list.push(extracted[idx].1.clone_ref(py));
        }
        result.push(col_list);
    }
    Ok(result)
}

#[pyfunction]
#[pyo3(signature = (pages_blocks, page_width=None))]
pub fn batch_sort_reading_order(
    py: Python<'_>,
    pages_blocks: Vec<Vec<Bound<'_, PyAny>>>,
    page_width: Option<f32>,
) -> PyResult<Vec<Vec<Py<PyAny>>>> {
    let mut extracted_pages: Vec<Vec<(TextBlock, Py<PyAny>)>> = Vec::with_capacity(pages_blocks.len());
    for page in pages_blocks {
        let mut page_blocks = Vec::with_capacity(page.len());
        for (i, obj) in page.into_iter().enumerate() {
            let block = extract_block(&obj, i)?;
            page_blocks.push((block, obj.unbind()));
        }
        extracted_pages.push(page_blocks);
    }

    let sorted_indices: Vec<Vec<usize>> = py.detach(|| {
        extracted_pages
            .par_iter()
            .map(|page_blocks| {
                let blocks_only: Vec<TextBlock> = page_blocks.iter().map(|(b, _)| b.clone()).collect();
                let width = page_width.unwrap_or_else(|| {
                    blocks_only.iter().map(|b| b.bbox.x1).fold(612.0_f32, f32::max)
                });
                sort_reading_order_indices(&blocks_only, width)
            })
            .collect()
    });

    let mut result = Vec::with_capacity(extracted_pages.len());
    for (page_blocks, indices) in extracted_pages.into_iter().zip(sorted_indices) {
        let mut sorted_page = Vec::with_capacity(indices.len());
        for idx in indices {
            sorted_page.push(page_blocks[idx].1.clone_ref(py));
        }
        result.push(sorted_page);
    }

    Ok(result)
}

#[derive(Clone, Copy)]
pub struct CellMeta {
    pub row: usize,
    pub col: usize,
    pub rowspan: usize,
    pub colspan: usize,
}

fn snap_coordinates(coords: &[f32], tol: f32) -> Vec<f32> {
    if coords.is_empty() {
        return Vec::new();
    }
    let mut sorted = coords.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let mut snapped = Vec::new();
    let mut current_cluster = vec![sorted[0]];

    for &val in &sorted[1..] {
        let mean: f32 = current_cluster.iter().sum::<f32>() / current_cluster.len() as f32;
        if (val - mean).abs() <= tol {
            current_cluster.push(val);
        } else {
            snapped.push(mean);
            current_cluster = vec![val];
        }
    }
    if !current_cluster.is_empty() {
        snapped.push(current_cluster.iter().sum::<f32>() / current_cluster.len() as f32);
    }
    snapped
}

fn closest_boundary(boundaries: &[f32], val: f32) -> usize {
    boundaries
        .iter()
        .enumerate()
        .min_by(|(_, a), (_, b)| {
            (val - *a).abs().partial_cmp(&(val - *b).abs()).unwrap_or(std::cmp::Ordering::Equal)
        })
        .map(|(idx, _)| idx)
        .unwrap_or(0)
}

fn compute_table_grid(cell_data: &[(BoundingBox, Py<PyAny>)], tol: f32) -> (Vec<Vec<usize>>, Vec<CellMeta>) {
    let mut x_coords = Vec::new();
    let mut y_coords = Vec::new();

    for (bbox, _) in cell_data {
        x_coords.push(bbox.x0);
        x_coords.push(bbox.x1);
        y_coords.push(bbox.y0);
        y_coords.push(bbox.y1);
    }

    let x_snapped = snap_coordinates(&x_coords, tol);
    let y_snapped = snap_coordinates(&y_coords, tol);

    let mut metadata = Vec::with_capacity(cell_data.len());
    let mut rows_map: std::collections::BTreeMap<usize, Vec<(usize, usize)>> = std::collections::BTreeMap::new();

    for (idx, (bbox, _)) in cell_data.iter().enumerate() {
        let col_start = closest_boundary(&x_snapped, bbox.x0);
        let col_end = closest_boundary(&x_snapped, bbox.x1).max(col_start + 1);
        let row_start = closest_boundary(&y_snapped, bbox.y0);
        let row_end = closest_boundary(&y_snapped, bbox.y1).max(row_start + 1);

        let colspan = (col_end - col_start).max(1);
        let rowspan = (row_end - row_start).max(1);

        metadata.push(CellMeta {
            row: row_start,
            col: col_start,
            rowspan,
            colspan,
        });

        rows_map.entry(row_start).or_default().push((col_start, idx));
    }

    let mut grid_rows = Vec::new();
    for (_, mut row_cells) in rows_map {
        row_cells.sort_by_key(|&(col, _)| col);
        grid_rows.push(row_cells.into_iter().map(|(_, idx)| idx).collect());
    }

    (grid_rows, metadata)
}

#[pyfunction]
#[pyo3(signature = (cells, tolerance=3.0))]
pub fn reconstruct_table_grid(
    py: Python<'_>,
    cells: Vec<Bound<'_, PyAny>>,
    tolerance: f32,
) -> PyResult<Vec<Vec<Py<PyAny>>>> {
    if cells.is_empty() {
        return Ok(Vec::new());
    }

    let tol = if tolerance <= 0.0 { 3.0 } else { tolerance };

    let mut cell_data = Vec::with_capacity(cells.len());
    for (i, obj) in cells.into_iter().enumerate() {
        let block = extract_block(&obj, i)?;
        cell_data.push((block.bbox, obj.unbind()));
    }

    let (grid_rows, cell_metadata) = py.detach(|| {
        compute_table_grid(&cell_data, tol)
    });

    let mut result = Vec::with_capacity(grid_rows.len());
    for row_indices in grid_rows {
        let mut row_cells = Vec::with_capacity(row_indices.len());
        for idx in row_indices {
            let py_obj = &cell_data[idx].1;
            let meta = &cell_metadata[idx];
            let bound_obj = py_obj.bind(py);
            if let Ok(dict) = bound_obj.cast::<PyDict>() {
                dict.set_item("row", meta.row)?;
                dict.set_item("col", meta.col)?;
                dict.set_item("rowspan", meta.rowspan)?;
                dict.set_item("colspan", meta.colspan)?;
            }
            row_cells.push(py_obj.clone_ref(py));
        }
        result.push(row_cells);
    }

    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_bounding_box_dimensions() {
        let bbox = BoundingBox::new(10.0, 20.0, 110.0, 120.0);
        assert_eq!(bbox.width(), 100.0);
        assert_eq!(bbox.height(), 100.0);
    }

    #[test]
    fn test_snap_coordinates() {
        let coords = vec![10.0, 10.5, 11.0, 50.0, 50.2, 100.0];
        let snapped = snap_coordinates(&coords, 2.0);
        assert_eq!(snapped.len(), 3);
        assert!((snapped[0] - 10.5).abs() < 0.1);
        assert!((snapped[1] - 50.1).abs() < 0.1);
        assert!((snapped[2] - 100.0).abs() < 0.1);
    }

    #[test]
    fn test_closest_boundary() {
        let boundaries = vec![0.0, 50.0, 100.0];
        assert_eq!(closest_boundary(&boundaries, 5.0), 0);
        assert_eq!(closest_boundary(&boundaries, 48.0), 1);
        assert_eq!(closest_boundary(&boundaries, 95.0), 2);
    }

    #[test]
    fn test_detect_columns_single_column() {
        let blocks = vec![
            TextBlock { bbox: BoundingBox::new(50.0, 100.0, 500.0, 120.0), text: "Full width title".into(), original_index: 0 },
            TextBlock { bbox: BoundingBox::new(50.0, 130.0, 500.0, 150.0), text: "Full width para".into(), original_index: 1 },
        ];
        let columns = detect_columns_internal(&blocks, 600.0);
        assert_eq!(columns.len(), 1);
        assert_eq!(columns[0], vec![0, 1]);
    }

    #[test]
    fn test_detect_columns_two_columns() {
        let blocks = vec![
            TextBlock { bbox: BoundingBox::new(320.0, 100.0, 550.0, 120.0), text: "Col 2 top".into(), original_index: 0 },
            TextBlock { bbox: BoundingBox::new(50.0, 100.0, 280.0, 120.0), text: "Col 1 top".into(), original_index: 1 },
            TextBlock { bbox: BoundingBox::new(50.0, 130.0, 280.0, 150.0), text: "Col 1 bottom".into(), original_index: 2 },
            TextBlock { bbox: BoundingBox::new(320.0, 130.0, 550.0, 150.0), text: "Col 2 bottom".into(), original_index: 3 },
        ];
        let columns = detect_columns_internal(&blocks, 600.0);
        assert_eq!(columns.len(), 2);
        assert_eq!(columns[0], vec![1, 2]); // Left column in reading order
        assert_eq!(columns[1], vec![0, 3]); // Right column in reading order
    }

    #[test]
    fn test_sort_reading_order_indices() {
        let blocks = vec![
            TextBlock { bbox: BoundingBox::new(320.0, 100.0, 550.0, 120.0), text: "Col 2 top".into(), original_index: 0 },
            TextBlock { bbox: BoundingBox::new(50.0, 100.0, 280.0, 120.0), text: "Col 1 top".into(), original_index: 1 },
        ];
        let indices = sort_reading_order_indices(&blocks, 600.0);
        assert_eq!(indices, vec![1, 0]);
    }
}
